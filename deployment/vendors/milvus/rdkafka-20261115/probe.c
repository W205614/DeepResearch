#include <stdio.h>
#include <string.h>
#include "rdkafka.h"

int main(int argc, char **argv) {
    static const char expected_features[] =
        "snappy,ssl,sasl,regex,lz4,sasl_gssapi,sasl_plain,sasl_scram,zstd,sasl_oauthbearer";
    const char *codecs[] = {"none", "snappy", "lz4", "zstd"};
    const char *mechanisms[] = {"GSSAPI", "PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512", "OAUTHBEARER"};
    rd_kafka_conf_t *conf = rd_kafka_conf_new();
    char value[4096], error[512];
    size_t size = sizeof(value);
    if (rd_kafka_version() != RD_KAFKA_VERSION || RD_KAFKA_VERSION != 0x020601ff)
        return 2;
    if (rd_kafka_conf_get(conf, "builtin.features", value, &size) != RD_KAFKA_CONF_OK)
        return 3;
    printf("rd_kafka_version=0x%08x\nrd_kafka_version_str=%s\nbuiltin.features=%s\n",
           rd_kafka_version(), rd_kafka_version_str(), value);
    if (strcmp(value, expected_features))
        return 4;
    for (size_t i = 0; i < sizeof(codecs) / sizeof(codecs[0]); ++i)
        if (rd_kafka_conf_set(conf, "compression.codec", codecs[i], error, sizeof(error)) != RD_KAFKA_CONF_OK)
            return 5;
    if (rd_kafka_conf_set(conf, "security.protocol", "SASL_SSL", error, sizeof(error)) != RD_KAFKA_CONF_OK)
        return 6;
    for (size_t i = 0; i < sizeof(mechanisms) / sizeof(mechanisms[0]); ++i)
        if (rd_kafka_conf_set(conf, "sasl.mechanisms", mechanisms[i], error, sizeof(error)) != RD_KAFKA_CONF_OK)
            return 7;
    rd_kafka_conf_destroy(conf);
    puts("ORIGINAL_FEATURES_AND_CONFIGURATION_CONTRACT_PASSED");
    if (argc == 2 && !strcmp(argv[1], "--unit")) {
        int failed = rd_kafka_unittest();
        printf("UPSTREAM_BUILTIN_UNIT_FAILURES=%d\n", failed);
        return failed ? 8 : 0;
    }
    return argc == 1 ? 0 : 9;
}
