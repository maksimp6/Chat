#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int alice_checked_copy(uint8_t *, size_t, const uint8_t *, size_t);

#define CHECK(condition)                                                           \
    do {                                                                           \
        if (!(condition)) {                                                         \
            fprintf(stderr, "%s:%d: failed: %s\n", __func__, __LINE__, #condition); \
            return 1;                                                              \
        }                                                                          \
    } while (0)

static int test_null_and_extreme_bounds(void) {
    uint8_t dst[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    uint8_t before[sizeof(dst)];
    const uint8_t src[8] = {8, 7, 6, 5, 4, 3, 2, 1};
    memcpy(before, dst, sizeof(dst));

    /* A zero-length operation must not dereference either pointer. */
    CHECK(alice_checked_copy(NULL, 0, NULL, 0) == 0);
    CHECK(alice_checked_copy(NULL, SIZE_MAX, NULL, 0) == 0);
    CHECK(alice_checked_copy(dst, 0, NULL, 0) == 0);
    CHECK(alice_checked_copy(NULL, 0, src, 0) == 0);
    CHECK(alice_checked_copy(dst, 0, src, 0) == 0);
    CHECK(memcmp(dst, before, sizeof(dst)) == 0);

    /* Bad pointers/lengths fail closed, without modifying destination. */
    CHECK(alice_checked_copy(NULL, 1, src, 1) == -1);
    CHECK(alice_checked_copy(dst, 1, NULL, 1) == -1);
    CHECK(alice_checked_copy(dst, 0, src, 1) == -1);
    CHECK(alice_checked_copy(dst, sizeof(dst), src, SIZE_MAX) == -1);
    CHECK(alice_checked_copy(dst, SIZE_MAX, NULL, 1) == -1);
    CHECK(memcmp(dst, before, sizeof(dst)) == 0);

    CHECK(alice_checked_copy(dst, sizeof(dst), src, sizeof(src)) == 0);
    CHECK(memcmp(dst, src, sizeof(dst)) == 0);
    return 0;
}

static int test_overlap_and_canaries(void) {
    static const struct {
        size_t source;
        size_t destination;
        size_t length;
        size_t capacity;
    } cases[] = {
        {4, 4, 16, 16},  /* exact alias */
        {4, 5, 16, 16},  /* forward overlap */
        {5, 4, 16, 16},  /* backward overlap */
        {4, 20, 16, 16}, /* touching ranges */
        {20, 4, 16, 16},
        {4, 5, 15, 16},  /* destination has slack */
        {4, 5, 17, 16},  /* rejected: no partial write */
        {4, 5, 0, 0},
    };
    for (size_t case_index = 0; case_index < sizeof(cases) / sizeof(cases[0]);
         ++case_index) {
        uint8_t actual[64];
        uint8_t expected[64];
        for (size_t i = 0; i < sizeof(actual); ++i) {
            actual[i] = (uint8_t)(i * 13u + 7u);
        }
        memcpy(expected, actual, sizeof(actual));
        int expected_status = cases[case_index].length <= cases[case_index].capacity ? 0 : -1;
        if (expected_status == 0) {
            memmove(expected + cases[case_index].destination,
                    expected + cases[case_index].source, cases[case_index].length);
        }
        int status = alice_checked_copy(actual + cases[case_index].destination,
                                        cases[case_index].capacity,
                                        actual + cases[case_index].source,
                                        cases[case_index].length);
        CHECK(status == expected_status);
        CHECK(memcmp(actual, expected, sizeof(actual)) == 0);
    }
    return 0;
}

static int test_exhaustive_small_bounded_inputs(void) {
    /* 17^2 * 33^2 = 314721 deterministic cases, including overlap and failure. */
    unsigned long cases = 0;
    for (size_t source = 0; source <= 16; ++source) {
        for (size_t destination = 0; destination <= 16; ++destination) {
            for (size_t capacity = 0; capacity <= 32; ++capacity) {
                for (size_t length = 0; length <= 32; ++length) {
                    uint8_t actual[64];
                    uint8_t expected[64];
                    for (size_t i = 0; i < sizeof(actual); ++i) {
                        actual[i] = (uint8_t)(i * 37u + source * 7u + destination * 11u);
                    }
                    memcpy(expected, actual, sizeof(actual));
                    int expected_status = length <= capacity ? 0 : -1;
                    if (expected_status == 0) {
                        memmove(expected + destination, expected + source, length);
                    }
                    int status = alice_checked_copy(actual + destination, capacity,
                                                    actual + source, length);
                    CHECK(status == expected_status);
                    CHECK(memcmp(actual, expected, sizeof(actual)) == 0);
                    ++cases;
                }
            }
        }
    }
    CHECK(cases == 314721UL);
    printf("checked_copy: %lu exhaustive cases plus negative/boundary tests\n", cases);
    return 0;
}

int main(void) {
    if (test_null_and_extreme_bounds() != 0 ||
        test_overlap_and_canaries() != 0 ||
        test_exhaustive_small_bounded_inputs() != 0) {
        return 1;
    }
    return 0;
}
