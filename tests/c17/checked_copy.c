#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* Caller owns dst. Returns 0 on success, -1 on invalid bounds. */
int alice_checked_copy(uint8_t *dst, size_t capacity,
                       const uint8_t *src, size_t length) {
    if (length > capacity || (length != 0 && (dst == NULL || src == NULL))) {
        return -1;
    }
    if (length != 0) {
        memmove(dst, src, length);
    }
    return 0;
}
