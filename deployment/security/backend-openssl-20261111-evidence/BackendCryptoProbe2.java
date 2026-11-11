import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import javax.net.ssl.*;

public class BackendCryptoProbe2 {
    static String json(Object value) {
        if (value == null) return "null";
        if (value instanceof Boolean || value instanceof Number) return value.toString();
        if (value instanceof Map<?, ?> map) {
            var fields = new ArrayList<String>();
            map.forEach((k, v) -> fields.add(json(k.toString()) + ":" + json(v)));
            return "{" + String.join(",", fields) + "}";
        }
        if (value instanceof Collection<?> list) return "[" + String.join(",", list.stream().map(BackendCryptoProbe2::json).toList()) + "]";
        return "\"" + value.toString().replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + "\"";
    }

    public static void main(String[] args) throws Exception {
        var result = new LinkedHashMap<String, Object>();
        result.put("java_version", System.getProperty("java.runtime.version"));
        result.put("user", System.getProperty("user.name"));
        result.put("default_ssl_provider", SSLContext.getDefault().getProvider().getName());
        result.put("default_ssl_protocols", List.of(SSLContext.getDefault().getDefaultSSLParameters().getProtocols()));
        result.put("http_client_versions", Arrays.stream(HttpClient.Version.values()).map(Enum::name).toList());
        result.put("providers", Arrays.stream(Security.getProviders()).map(p -> Map.of("name", p.getName(), "class", p.getClass().getName(), "services", p.getServices().size(), "info", p.getInfo())).toList());
        var overrides = new LinkedHashMap<String, Object>();
        for (String key : List.of("java.security.properties", "javax.net.ssl.keyStore", "javax.net.ssl.trustStore", "jdk.tls.client.protocols")) overrides.put(key, System.getProperty(key));
        result.put("security_overrides", overrides);

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        byte[] key = new byte[16], iv = new byte[12], text = "backend-probe".getBytes(StandardCharsets.UTF_8);
        SecureRandom random = new SecureRandom();
        random.nextBytes(key);
        random.nextBytes(iv);
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, iv));
        byte[] encrypted = cipher.doFinal(text);
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, iv));
        if (!Arrays.equals(cipher.doFinal(encrypted), text)) throw new AssertionError("JCE AES-GCM roundtrip failed");
        result.put("aes_gcm_provider", cipher.getProvider().getName());
        result.put("aes_gcm_roundtrip", Map.of("passed", true, "key_generation", "SecureRandom", "iv_generation", "SecureRandom", "key_bytes", key.length, "iv_bytes", iv.length));
        try {
            Cipher siv = Cipher.getInstance("AES/GCM-SIV/NoPadding");
            result.put("aes_gcm_siv", Map.of("available", true, "provider", siv.getProvider().getName()));
        } catch (GeneralSecurityException expected) {
            result.put("aes_gcm_siv", Map.of("available", false, "exception", expected.getClass().getName(), "message", expected.getMessage()));
        }

        Class<?> openSsl = Class.forName("io.netty.handler.ssl.OpenSsl");
        boolean nativeAvailable = (boolean) openSsl.getMethod("isAvailable").invoke(null);
        Throwable cause = (Throwable) openSsl.getMethod("unavailabilityCause").invoke(null);
        result.put("netty_openssl_available", nativeAvailable);
        result.put("netty_openssl_unavailability", cause == null ? null : cause.toString());
        Class<?> sslContext = Class.forName("io.netty.handler.ssl.SslContext");
        result.put("netty_default_client_provider", sslContext.getMethod("defaultClientProvider").invoke(null).toString());
        Class<?> sslOptions = Class.forName("io.lettuce.core.SslOptions");
        Object builder = sslOptions.getMethod("builder").invoke(null);
        Object options = builder.getClass().getMethod("build").invoke(builder);
        Object nettyBuilder = sslOptions.getMethod("createSslContextBuilder").invoke(options);
        Object lettuceContext = nettyBuilder.getClass().getMethod("build").invoke(nettyBuilder);
        result.put("lettuce_default_ssl_context_class", lettuceContext.getClass().getName());
        SSLSocketFactory postgres = (SSLSocketFactory) Class.forName("org.postgresql.ssl.LibPQFactory").getConstructor(Properties.class).newInstance(new Properties());
        Class<?> wrapped = postgres.getClass().getSuperclass();
        var factoryField = wrapped.getDeclaredField("factory");
        factoryField.setAccessible(true);
        Object actualPostgresFactory = factoryField.get(postgres);
        result.put("postgres_ssl_factory_class", postgres.getClass().getName());
        result.put("postgres_delegate_ssl_factory_class", actualPostgresFactory.getClass().getName());

        KeyStore store = KeyStore.getInstance("PKCS12");
        char[] password = "backend-probe-only".toCharArray();
        try (InputStream input = Files.newInputStream(Path.of("/probe/probe.p12"))) { store.load(input, password); }
        KeyManagerFactory keys = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        keys.init(store, password);
        TrustManagerFactory trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        trust.init(store);
        SSLContext serverContext = SSLContext.getInstance("TLS"), clientContext = SSLContext.getInstance("TLS");
        serverContext.init(keys.getKeyManagers(), trust.getTrustManagers(), new SecureRandom());
        clientContext.init(null, trust.getTrustManagers(), new SecureRandom());
        var failure = new AtomicReference<Throwable>();
        var handshake = new LinkedHashMap<String, Object>();
        try (var listener = (SSLServerSocket) serverContext.getServerSocketFactory().createServerSocket(0, 1, InetAddress.getLoopbackAddress())) {
            Thread worker = new Thread(() -> {
                try (var socket = (SSLSocket) listener.accept()) {
                    socket.setSoTimeout(5000);
                    socket.startHandshake();
                    handshake.put("protocol", socket.getSession().getProtocol());
                    handshake.put("cipher_suite", socket.getSession().getCipherSuite());
                    var reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.US_ASCII));
                    for (int count = 0; count < 100; count++) { String line = reader.readLine(); if (line == null || line.isEmpty()) break; }
                    socket.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\nprobe-ok".getBytes(StandardCharsets.US_ASCII));
                    socket.getOutputStream().flush();
                } catch (Throwable error) { failure.set(error); }
            }, "backend-probe-tls-loopback");
            worker.start();
            try (HttpClient client = HttpClient.newBuilder().sslContext(clientContext).connectTimeout(java.time.Duration.ofSeconds(5)).build()) {
                var response = client.send(HttpRequest.newBuilder(URI.create("https://localhost:" + listener.getLocalPort())).timeout(java.time.Duration.ofSeconds(5)).GET().build(), HttpResponse.BodyHandlers.ofString());
                if (response.statusCode() != 200 || !response.body().equals("probe-ok")) throw new AssertionError("Actual JDK HTTPS probe failed");
                handshake.put("http_status", response.statusCode());
                handshake.put("response", response.body());
                handshake.put("http_client_ssl_provider", client.sslContext().getProvider().getName());
            }
            worker.join(6000);
            if (worker.isAlive() || failure.get() != null) throw new AssertionError("Actual local TLS server failed", failure.get());
        }
        result.put("actual_loopback_tls", handshake);
        var libraries = new TreeSet<String>();
        for (String line : Files.readAllLines(Path.of("/proc/self/maps"))) {
            int start = line.indexOf('/');
            if (start >= 0 && line.substring(start).contains(".so")) libraries.add(line.substring(start));
        }
        result.put("loaded_shared_objects_after_tls", libraries);
        result.put("paid_or_external_requests", 0);
        result.put("passed", !nativeAvailable && result.get("default_ssl_provider").equals("SunJSSE")
                && result.get("netty_default_client_provider").equals("JDK")
                && lettuceContext.getClass().getName().equals("io.netty.handler.ssl.JdkSslClientContext")
                && libraries.stream().noneMatch(x -> x.contains("libssl.so") || x.contains("libcrypto.so")));
        System.out.println(json(result));
        if (!Boolean.TRUE.equals(result.get("passed"))) throw new AssertionError("Observed actual crypto/provider boundary differs");
    }
}
