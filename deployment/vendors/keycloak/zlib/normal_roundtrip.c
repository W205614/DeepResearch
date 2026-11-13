#include <stdio.h>
#include <string.h>
#include <zlib.h>

int main(void) {
    const unsigned char input[] = "Keycloak normal zlib roundtrip\n";
    unsigned char compressed[256], output[256];
    uLongf compressed_size = sizeof(compressed), output_size = sizeof(output);
    if (strcmp(zlibVersion(), "1.4") ||
        compress2(compressed, &compressed_size, input, sizeof(input), Z_DEFAULT_COMPRESSION) != Z_OK ||
        uncompress(output, &output_size, compressed, compressed_size) != Z_OK ||
        output_size != sizeof(input) || memcmp(input, output, sizeof(input))) return 1;
    gzFile stream = gzopen("/tmp/normal-zlib.gz", "wb");
    if (!stream || gzwrite(stream, input, 8) != 8 ||
        gzwrite(stream, input + 8, sizeof(input) - 8) != sizeof(input) - 8 || gzclose(stream) != Z_OK) return 2;
    stream = gzopen("/tmp/normal-zlib.gz", "rb");
    if (!stream || gzread(stream, output, sizeof(output)) != sizeof(input) ||
        memcmp(input, output, sizeof(input)) || gzclose(stream) != Z_OK) return 3;
    printf("zlib=%s normal compress/uncompress and regular-file gzip roundtrips passed\n", zlibVersion());
    return 0;
}
