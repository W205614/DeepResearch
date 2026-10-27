package utils

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"path/filepath"
	"testing"
	"time"

	"google.golang.org/grpc"

	"github.com/milvus-io/milvus/pkg/v3/util/paramtable"
)

type internalTLSFixture struct {
	ca, cert, key, missing, invalid string
}

func internalTLSFiles(t *testing.T) internalTLSFixture {
	t.Helper()
	dir := t.TempDir()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "unit-test CA"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	serverKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	server := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "localhost"},
		DNSNames: []string{"localhost"}, NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	serverDER, err := x509.CreateCertificate(rand.Reader, server, ca, &serverKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalECPrivateKey(serverKey)
	if err != nil {
		t.Fatal(err)
	}
	files := internalTLSFixture{ca: filepath.Join(dir, "ca.pem"), cert: filepath.Join(dir, "server.pem"),
		key: filepath.Join(dir, "server.key"), missing: filepath.Join(dir, "not-created"), invalid: filepath.Join(dir, "invalid")}
	for path, data := range map[string][]byte{
		files.ca:      pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}),
		files.cert:    pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: serverDER}),
		files.key:     pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}),
		files.invalid: []byte("not a PEM certificate or key"),
	} {
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return files
}

func configureInternalTLS(t *testing.T, enabled, ca, cert, key string) {
	t.Helper()
	paramtable.Init()
	params := paramtable.Get()
	values := map[string]string{
		params.InternalTLSCfg.InternalTLSEnabled.Key:       enabled,
		params.InternalTLSCfg.InternalTLSCaPemPath.Key:     ca,
		params.InternalTLSCfg.InternalTLSServerPemPath.Key: cert,
		params.InternalTLSCfg.InternalTLSServerKeyPath.Key: key,
	}
	previous := make(map[string]string, len(values))
	for name, value := range values {
		previous[name] = params.GetWithDefault(name, "")
		if err := params.Save(name, value); err != nil {
			t.Fatal("failed to set internal TLS unit configuration")
		}
	}
	t.Cleanup(func() {
		for name, value := range previous {
			if err := params.Save(name, value); err != nil {
				t.Error("failed to restore internal TLS unit configuration")
			}
		}
	})
}

func TestInternalTLSCredentialsRejectMissingOrInvalidFiles(t *testing.T) {
	files := internalTLSFiles(t)
	for _, test := range []struct {
		name, ca, cert, key, message string
	}{
		{"missing-ca", files.missing, files.cert, files.key, "internal TLS CA configuration is unreadable or invalid"},
		{"invalid-ca", files.invalid, files.cert, files.key, "internal TLS CA configuration is unreadable or invalid"},
		{"missing-cert", files.ca, files.missing, files.key, "internal TLS server certificate/key configuration is unreadable or invalid"},
		{"invalid-cert", files.ca, files.invalid, files.key, "internal TLS server certificate/key configuration is unreadable or invalid"},
		{"missing-key", files.ca, files.cert, files.missing, "internal TLS server certificate/key configuration is unreadable or invalid"},
		{"invalid-key", files.ca, files.cert, files.invalid, "internal TLS server certificate/key configuration is unreadable or invalid"},
	} {
		t.Run(test.name, func(t *testing.T) {
			configureInternalTLS(t, "true", test.ca, test.cert, test.key)
			reachedServerConstruction := false
			defer func() {
				if value := recover(); value != test.message {
					t.Fatalf("expected sanitized credential initialization failure, got %T", value)
				}
				if reachedServerConstruction {
					t.Fatal("invalid TLS configuration reached grpc.NewServer")
				}
			}()
			option := EnableInternalTLS("unit-test")
			reachedServerConstruction = true
			server := grpc.NewServer(option)
			server.Stop()
		})
	}
}

func TestInternalTLSCredentialsValidAndDisabled(t *testing.T) {
	files := internalTLSFiles(t)
	t.Run("valid-files", func(t *testing.T) {
		configureInternalTLS(t, "true", files.ca, files.cert, files.key)
		if getTLSCreds(files.cert, files.key, "unit-test") == nil {
			t.Fatal("valid configuration returned nil credentials")
		}
		server := grpc.NewServer(EnableInternalTLS("unit-test"))
		server.Stop()
	})
	t.Run("disabled-skips-missing-files", func(t *testing.T) {
		configureInternalTLS(t, "false", files.missing, files.missing, files.missing)
		server := grpc.NewServer(EnableInternalTLS("unit-test"))
		server.Stop()
	})
}
