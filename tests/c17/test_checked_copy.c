#include <stdio.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

int alice_checked_copy(uint8_t *, size_t, const uint8_t *, size_t);

#define CHECK(condition) do { if (!(condition)) { fprintf(stderr, "check failed: %s at line %d\n", #condition, __LINE__); return 1; } } while (0)

int main(void) {
    uint8_t dst[4] = {0};
    uint8_t src[4] = {1, 2, 3, 4};
    CHECK(alice_checked_copy(dst, sizeof(dst), src, sizeof(src)) == 0);
    CHECK(memcmp(dst, src, sizeof(src)) == 0);
    CHECK(alice_checked_copy(dst, 2, src, 3) == -1);
    CHECK(alice_checked_copy(NULL, 0, src, 1) == -1);
    CHECK(alice_checked_copy(dst, 4, NULL, 1) == -1);
    CHECK(alice_checked_copy(NULL, 0, NULL, 0) == 0);
    CHECK(alice_checked_copy(dst, 4, dst, 4) == 0);
    return 0;
}
