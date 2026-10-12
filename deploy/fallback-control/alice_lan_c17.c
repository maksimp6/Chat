/*
 * alice-lan-c17: self-hosted fallback control server for Alice Pro (#1178).
 *
 * A loopback-only HTTP/1.1 command queue. An owner-controlled TLS ingress
 * terminates HTTPS in front of it; the phone agent connects outbound, long-polls
 * for commands and acknowledges them. No vendor tunnel, SaaS or API key.
 *
 * Process model: one thread, one poll(2) event loop. All shared state is owned by
 * that thread, so no locks are needed inside the process. A POSIX record lock on
 * <state-dir>/lock keeps a second process off the same store.
 *
 * Durability: every accepted create/ack is appended to <state-dir>/journal as a
 * length-prefixed, CRC32-checked record and fdatasync'd before the HTTP response
 * is sent. On start the journal is replayed; a torn final record (crash during
 * append, never acknowledged to a client) is truncated, any other damage makes the
 * server refuse to start.
 *
 * Roles: the admin token may create and inspect commands; the agent token may
 * poll and acknowledge. Neither can do the other's operations.
 *
 * Wire contract: see tests/test_alice_c17_tunnel_red.py and
 * deploy/fallback-control/README.md.
 */
#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

enum {
    MAX_CONNECTIONS = 128,
    HEADER_MAX = 16384,
    MAX_HEADERS = 64,
    TARGET_MAX = 512,
    BODY_MAX = 65536,
    LINGER_DRAIN_MAX = 2 * 1024 * 1024,
    ID_MAX = 128,
    KIND_MAX = 64,
    STATUS_MAX = 16,
    TOKEN_MIN = 16,
    TOKEN_MAX = 4096,
    JSON_DEPTH_MAX = 32,
    MAX_COMMANDS = 65536,
    MAX_PENDING = 1024,
    POLL_BATCH = 32,
    POLL_TIMEOUT_MAX_MS = 30000,
    IDLE_TIMEOUT_MS = 10000,
    LINGER_TIMEOUT_MS = 1000,
    LEASE_MS = 30000,
    HASH_SLOTS = 131072 /* power of two, > 2 * MAX_COMMANDS */
};

#define PAYLOAD_TOTAL_MAX ((size_t)64 * 1024 * 1024)
#define JOURNAL_MAX ((off_t)256 * 1024 * 1024)

/* ------------------------------------------------------------------ utils */

static volatile sig_atomic_t stop_requested = 0;

static void on_stop_signal(int signo)
{
    (void)signo;
    stop_requested = 1;
}

static void fail(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("alice-lan-c17: ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

static int64_t now_ms(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        return 0;
    }
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static uint32_t crc32_bytes(uint32_t crc, const unsigned char *data, size_t len)
{
    crc = ~crc;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
    }
    return ~crc;
}

static uint32_t fnv1a(const char *s, size_t len)
{
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < len; i++) {
        h ^= (unsigned char)s[i];
        h *= 16777619u;
    }
    return h;
}

static bool write_all(int fd, const void *buf, size_t len)
{
    const unsigned char *p = buf;
    while (len > 0) {
        ssize_t n = write(fd, p, len);
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            return false;
        }
        p += (size_t)n;
        len -= (size_t)n;
    }
    return true;
}

static bool ascii_ieq(const char *a, size_t alen, const char *b)
{
    size_t blen = strlen(b);
    if (alen != blen) {
        return false;
    }
    for (size_t i = 0; i < alen; i++) {
        unsigned char x = (unsigned char)a[i];
        unsigned char y = (unsigned char)b[i];
        if (x >= 'A' && x <= 'Z') {
            x = (unsigned char)(x - 'A' + 'a');
        }
        if (y >= 'A' && y <= 'Z') {
            y = (unsigned char)(y - 'A' + 'a');
        }
        if (x != y) {
            return false;
        }
    }
    return true;
}

/* Constant-time over the expected token; length mismatch still scans it. */
static bool token_equal(const char *given, size_t given_len, const char *expected, size_t expected_len)
{
    unsigned char diff = (unsigned char)(given_len != expected_len);
    for (size_t i = 0; i < expected_len; i++) {
        unsigned char g = i < given_len ? (unsigned char)given[i] : 0;
        diff |= (unsigned char)(g ^ (unsigned char)expected[i]);
    }
    return diff == 0;
}

/* ID charset: [A-Za-z0-9._-], 1..ID_MAX, not "." / ".." / "poll". */
static bool valid_id(const char *s, size_t len)
{
    if (len == 0 || len > ID_MAX) {
        return false;
    }
    for (size_t i = 0; i < len; i++) {
        char c = s[i];
        bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' ||
                  c == '_' || c == '-';
        if (!ok) {
            return false;
        }
    }
    if ((len == 1 && s[0] == '.') || (len == 2 && s[0] == '.' && s[1] == '.')) {
        return false;
    }
    if (len == 4 && memcmp(s, "poll", 4) == 0) {
        return false;
    }
    return true;
}

/* Kind charset: [a-z0-9._-], 1..KIND_MAX. */
static bool valid_kind(const char *s, size_t len)
{
    if (len == 0 || len > KIND_MAX) {
        return false;
    }
    for (size_t i = 0; i < len; i++) {
        char c = s[i];
        bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-';
        if (!ok) {
            return false;
        }
    }
    return true;
}

/* ------------------------------------------------------------------- JSON */

/*
 * Strict RFC 8259 validator with UTF-8 checking and a depth bound. Returns a
 * pointer just past the value, or NULL when the input is not valid JSON.
 */
static const char *json_ws(const char *p, const char *end)
{
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) {
        p++;
    }
    return p;
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

/* Validates one UTF-8 sequence starting at p; returns its length or 0. */
static size_t utf8_seq(const unsigned char *p, const unsigned char *end)
{
    unsigned char c = p[0];
    size_t need;
    uint32_t cp;
    if (c < 0x80) {
        return 1;
    } else if (c >= 0xC2 && c <= 0xDF) {
        need = 1;
        cp = c & 0x1Fu;
    } else if (c >= 0xE0 && c <= 0xEF) {
        need = 2;
        cp = c & 0x0Fu;
    } else if (c >= 0xF0 && c <= 0xF4) {
        need = 3;
        cp = c & 0x07u;
    } else {
        return 0;
    }
    if ((size_t)(end - p) <= need) {
        return 0;
    }
    for (size_t i = 1; i <= need; i++) {
        if ((p[i] & 0xC0u) != 0x80u) {
            return 0;
        }
        cp = (cp << 6) | (p[i] & 0x3Fu);
    }
    if ((need == 2 && cp < 0x800u) || (need == 3 && (cp < 0x10000u || cp > 0x10FFFFu)) ||
        (cp >= 0xD800u && cp <= 0xDFFFu)) {
        return 0;
    }
    return need + 1;
}

static const char *json_string(const char *p, const char *end, bool *has_escape)
{
    if (p >= end || *p != '"') {
        return NULL;
    }
    p++;
    while (p < end) {
        unsigned char c = (unsigned char)*p;
        if (c == '"') {
            return p + 1;
        }
        if (c < 0x20) {
            return NULL;
        }
        if (c == '\\') {
            if (has_escape != NULL) {
                *has_escape = true;
            }
            p++;
            if (p >= end) {
                return NULL;
            }
            switch (*p) {
            case '"':
            case '\\':
            case '/':
            case 'b':
            case 'f':
            case 'n':
            case 'r':
            case 't':
                p++;
                break;
            case 'u':
                if (end - p < 5) {
                    return NULL;
                }
                for (int i = 1; i <= 4; i++) {
                    if (hexval(p[i]) < 0) {
                        return NULL;
                    }
                }
                p += 5;
                break;
            default:
                return NULL;
            }
            continue;
        }
        size_t n = utf8_seq((const unsigned char *)p, (const unsigned char *)end);
        if (n == 0) {
            return NULL;
        }
        p += n;
    }
    return NULL;
}

static const char *json_number(const char *p, const char *end)
{
    if (p < end && *p == '-') {
        p++;
    }
    if (p >= end) {
        return NULL;
    }
    if (*p == '0') {
        p++;
    } else if (*p >= '1' && *p <= '9') {
        while (p < end && *p >= '0' && *p <= '9') {
            p++;
        }
    } else {
        return NULL;
    }
    if (p < end && *p == '.') {
        p++;
        if (p >= end || *p < '0' || *p > '9') {
            return NULL;
        }
        while (p < end && *p >= '0' && *p <= '9') {
            p++;
        }
    }
    if (p < end && (*p == 'e' || *p == 'E')) {
        p++;
        if (p < end && (*p == '+' || *p == '-')) {
            p++;
        }
        if (p >= end || *p < '0' || *p > '9') {
            return NULL;
        }
        while (p < end && *p >= '0' && *p <= '9') {
            p++;
        }
    }
    return p;
}

static const char *json_literal(const char *p, const char *end, const char *lit)
{
    size_t n = strlen(lit);
    if ((size_t)(end - p) < n || memcmp(p, lit, n) != 0) {
        return NULL;
    }
    return p + n;
}

static const char *json_value(const char *p, const char *end, int depth);

static const char *json_container(const char *p, const char *end, int depth, bool object)
{
    if (depth > JSON_DEPTH_MAX) {
        return NULL;
    }
    char close = object ? '}' : ']';
    p = json_ws(p + 1, end);
    if (p < end && *p == close) {
        return p + 1;
    }
    for (;;) {
        if (object) {
            p = json_string(p, end, NULL);
            if (p == NULL) {
                return NULL;
            }
            p = json_ws(p, end);
            if (p >= end || *p != ':') {
                return NULL;
            }
            p = json_ws(p + 1, end);
        }
        p = json_value(p, end, depth + 1);
        if (p == NULL) {
            return NULL;
        }
        p = json_ws(p, end);
        if (p >= end) {
            return NULL;
        }
        if (*p == close) {
            return p + 1;
        }
        if (*p != ',') {
            return NULL;
        }
        p = json_ws(p + 1, end);
    }
}

static const char *json_value(const char *p, const char *end, int depth)
{
    if (p >= end) {
        return NULL;
    }
    switch (*p) {
    case '{':
        return json_container(p, end, depth, true);
    case '[':
        return json_container(p, end, depth, false);
    case '"':
        return json_string(p, end, NULL);
    case 't':
        return json_literal(p, end, "true");
    case 'f':
        return json_literal(p, end, "false");
    case 'n':
        return json_literal(p, end, "null");
    default:
        return json_number(p, end);
    }
}

typedef struct {
    const char *name;
    char type; /* 's' string (no escapes), 'o' object */
    const char *start;
    size_t len;
    bool seen;
} json_field;

enum { JSON_OK = 0, JSON_SYNTAX = 1, JSON_SCHEMA = 2 };

/*
 * Parses a top-level object whose members must be exactly `fields` (each once).
 * String fields return the raw text between the quotes and must not contain
 * escapes; object fields return the whole object text.
 */
static int json_object_fields(const char *body, size_t len, json_field *fields, size_t nfields)
{
    const char *end = body + len;
    const char *p = json_ws(body, end);
    const char *after = json_value(p, end, 0);
    if (after == NULL || json_ws(after, end) != end) {
        return JSON_SYNTAX;
    }
    if (*p != '{') {
        return JSON_SCHEMA;
    }
    p = json_ws(p + 1, end);
    if (*p != '}') {
        for (;;) {
            bool key_escaped = false;
            const char *kstart = p + 1;
            const char *kend = json_string(p, end, &key_escaped);
            p = json_ws(kend, end) + 1; /* skip ':' (already validated) */
            p = json_ws(p, end);
            const char *vstart = p;
            const char *vend = json_value(p, end, 1);
            json_field *match = NULL;
            for (size_t i = 0; i < nfields && !key_escaped; i++) {
                size_t klen = (size_t)(kend - 1 - kstart);
                if (strlen(fields[i].name) == klen && memcmp(fields[i].name, kstart, klen) == 0) {
                    match = &fields[i];
                }
            }
            if (match == NULL || match->seen) {
                return JSON_SCHEMA;
            }
            match->seen = true;
            if (match->type == 's') {
                bool escaped = false;
                if (*vstart != '"' || json_string(vstart, end, &escaped) == NULL || escaped) {
                    return JSON_SCHEMA;
                }
                match->start = vstart + 1;
                match->len = (size_t)(vend - vstart) - 2;
            } else {
                if (*vstart != '{') {
                    return JSON_SCHEMA;
                }
                match->start = vstart;
                match->len = (size_t)(vend - vstart);
            }
            p = json_ws(vend, end);
            if (*p == '}') {
                break;
            }
            p = json_ws(p + 1, end); /* ',' */
        }
    }
    for (size_t i = 0; i < nfields; i++) {
        if (!fields[i].seen) {
            return JSON_SCHEMA;
        }
    }
    return JSON_OK;
}

/* ------------------------------------------------------------------ store */

enum { STATE_QUEUED = 0, STATE_DELIVERED = 1, STATE_DONE = 2, STATE_FAILED = 3 };

static const char *state_name(int state)
{
    switch (state) {
    case STATE_QUEUED:
        return "queued";
    case STATE_DELIVERED:
        return "delivered";
    case STATE_DONE:
        return "done";
    default:
        return "failed";
    }
}

typedef struct {
    char id[ID_MAX + 1];
    size_t id_len;
    char kind[KIND_MAX + 1];
    char *payload; /* validated JSON object text, owned */
    size_t payload_len;
    int state;
    int64_t lease_until;
} command;

typedef struct {
    command *items;
    size_t count;
    size_t capacity;
    uint32_t *slots; /* index + 1, 0 = empty */
    size_t pending;
    size_t payload_bytes;
    size_t queue_scan; /* first index that may still be queued/delivered */
    int journal_fd;
    off_t journal_size;
} store;

static store g_store;

static command *store_find(const char *id, size_t len)
{
    uint32_t mask = HASH_SLOTS - 1;
    uint32_t h = fnv1a(id, len) & mask;
    for (;;) {
        uint32_t slot = g_store.slots[h];
        if (slot == 0) {
            return NULL;
        }
        command *c = &g_store.items[slot - 1];
        if (c->id_len == len && memcmp(c->id, id, len) == 0) {
            return c;
        }
        h = (h + 1) & mask;
    }
}

static bool store_insert(const char *id, size_t id_len, const char *kind, size_t kind_len, const char *payload,
                         size_t payload_len)
{
    if (g_store.count >= MAX_COMMANDS) {
        return false;
    }
    if (g_store.count == g_store.capacity) {
        size_t cap = g_store.capacity == 0 ? 256 : g_store.capacity * 2;
        if (cap > MAX_COMMANDS) {
            cap = MAX_COMMANDS;
        }
        command *items = realloc(g_store.items, cap * sizeof(*items));
        if (items == NULL) {
            return false;
        }
        g_store.items = items;
        g_store.capacity = cap;
    }
    char *copy = malloc(payload_len + 1);
    if (copy == NULL) {
        return false;
    }
    memcpy(copy, payload, payload_len);
    copy[payload_len] = '\0';
    command *c = &g_store.items[g_store.count];
    memset(c, 0, sizeof(*c));
    memcpy(c->id, id, id_len);
    c->id_len = id_len;
    memcpy(c->kind, kind, kind_len);
    c->payload = copy;
    c->payload_len = payload_len;
    c->state = STATE_QUEUED;
    uint32_t mask = HASH_SLOTS - 1;
    uint32_t h = fnv1a(id, id_len) & mask;
    while (g_store.slots[h] != 0) {
        h = (h + 1) & mask;
    }
    g_store.count++;
    g_store.slots[h] = (uint32_t)g_store.count;
    g_store.pending++;
    g_store.payload_bytes += payload_len;
    return true;
}

static void store_finish(command *c, int state)
{
    c->state = state;
    g_store.pending--;
}

/*
 * Record: "R1 " type ' ' (len ':' bytes ' ')* crc32-hex8 '\n'
 * The CRC covers everything from the type byte up to the final space.
 */
static bool journal_append(char type, const char *const *fields, const size_t *lens, size_t nfields)
{
    size_t total = 3 + 2 + 9 + 1;
    for (size_t i = 0; i < nfields; i++) {
        total += 21 + lens[i] + 1;
    }
    char *rec = malloc(total);
    if (rec == NULL) {
        return false;
    }
    size_t n = 0;
    memcpy(rec, "R1 ", 3);
    n = 3;
    rec[n++] = type;
    rec[n++] = ' ';
    for (size_t i = 0; i < nfields; i++) {
        int w = snprintf(rec + n, total - n, "%zu:", lens[i]);
        if (w < 0 || (size_t)w >= total - n) {
            free(rec);
            return false;
        }
        n += (size_t)w;
        memcpy(rec + n, fields[i], lens[i]);
        n += lens[i];
        rec[n++] = ' ';
    }
    uint32_t crc = crc32_bytes(0, (const unsigned char *)rec + 3, n - 3);
    int w = snprintf(rec + n, total - n, "%08x\n", (unsigned)crc);
    if (w != 9) {
        free(rec);
        return false;
    }
    n += 9;
    off_t before = g_store.journal_size;
    bool ok = write_all(g_store.journal_fd, rec, n) && fdatasync(g_store.journal_fd) == 0;
    free(rec);
    if (!ok) {
        /* Never leave an unacknowledged partial record that a later append could
         * bury in the middle of the journal. */
        if (ftruncate(g_store.journal_fd, before) != 0 || lseek(g_store.journal_fd, before, SEEK_SET) < 0) {
            fail("journal rollback failed; refusing to continue");
            exit(3);
        }
        return false;
    }
    g_store.journal_size = before + (off_t)n;
    return true;
}

enum { REC_OK = 0, REC_TORN = 1, REC_CORRUPT = 2 };

static int parse_len_field(const char *buf, size_t size, size_t *pos, const char **out, size_t *out_len)
{
    size_t p = *pos;
    size_t value = 0;
    size_t digits = 0;
    while (p < size && buf[p] >= '0' && buf[p] <= '9') {
        if (digits >= 9) {
            return REC_CORRUPT;
        }
        value = value * 10 + (size_t)(buf[p] - '0');
        digits++;
        p++;
    }
    if (p >= size) {
        return REC_TORN;
    }
    if (digits == 0 || buf[p] != ':') {
        return REC_CORRUPT;
    }
    p++;
    if (value > size - p) {
        return REC_TORN;
    }
    *out = buf + p;
    *out_len = value;
    p += value;
    if (p >= size) {
        return REC_TORN;
    }
    if (buf[p] != ' ') {
        return REC_CORRUPT;
    }
    *pos = p + 1;
    return REC_OK;
}

static int journal_record(const char *buf, size_t size, size_t *pos)
{
    size_t start = *pos;
    size_t p = start;
    if (size - p < 5) {
        return REC_TORN;
    }
    if (memcmp(buf + p, "R1 ", 3) != 0 || buf[p + 4] != ' ') {
        return REC_CORRUPT;
    }
    char type = buf[p + 3];
    size_t nfields = type == 'C' ? 3 : type == 'A' ? 2 : 0;
    if (nfields == 0) {
        return REC_CORRUPT;
    }
    p += 5;
    const char *f[3];
    size_t fl[3];
    for (size_t i = 0; i < nfields; i++) {
        int r = parse_len_field(buf, size, &p, &f[i], &fl[i]);
        if (r != REC_OK) {
            return r;
        }
    }
    if (size - p < 9) {
        return REC_TORN;
    }
    if (buf[p + 8] != '\n') {
        return REC_CORRUPT;
    }
    uint32_t expect = crc32_bytes(0, (const unsigned char *)buf + start + 3, p - start - 3);
    uint32_t got = 0;
    for (int i = 0; i < 8; i++) {
        int v = hexval(buf[p + (size_t)i]);
        if (v < 0) {
            return REC_CORRUPT;
        }
        got = (got << 4) | (uint32_t)v;
    }
    if (got != expect) {
        return REC_CORRUPT;
    }
    *pos = p + 9;
    if (type == 'C') {
        if (!valid_id(f[0], fl[0]) || !valid_kind(f[1], fl[1]) || store_find(f[0], fl[0]) != NULL) {
            return REC_CORRUPT;
        }
        const char *after = json_value(f[2], f[2] + fl[2], 0);
        if (fl[2] == 0 || f[2][0] != '{' || after != f[2] + fl[2]) {
            return REC_CORRUPT;
        }
        if (!store_insert(f[0], fl[0], f[1], fl[1], f[2], fl[2])) {
            return REC_CORRUPT;
        }
    } else {
        command *c = store_find(f[0], fl[0]);
        if (c == NULL || c->state == STATE_DONE || c->state == STATE_FAILED) {
            return REC_CORRUPT;
        }
        if (fl[1] == 4 && memcmp(f[1], "done", 4) == 0) {
            store_finish(c, STATE_DONE);
        } else if (fl[1] == 6 && memcmp(f[1], "failed", 6) == 0) {
            store_finish(c, STATE_FAILED);
        } else {
            return REC_CORRUPT;
        }
    }
    return REC_OK;
}

static bool journal_load(int dirfd)
{
    int fd = openat(dirfd, "journal", O_RDWR | O_CREAT | O_APPEND | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) {
        fail("cannot open journal: %s", strerror(errno));
        return false;
    }
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || (st.st_mode & 077) != 0 || st.st_size > JOURNAL_MAX) {
        fail("journal must be a private regular file of bounded size");
        close(fd);
        return false;
    }
    if (fsync(dirfd) != 0) {
        fail("cannot sync state directory: %s", strerror(errno));
        close(fd);
        return false;
    }
    size_t size = (size_t)st.st_size;
    char *buf = malloc(size + 1);
    if (buf == NULL) {
        close(fd);
        return false;
    }
    size_t got = 0;
    while (got < size) {
        ssize_t n = pread(fd, buf + got, size - got, (off_t)got);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            fail("cannot read journal");
            free(buf);
            close(fd);
            return false;
        }
        got += (size_t)n;
    }
    size_t pos = 0;
    while (pos < size) {
        size_t before = pos;
        int r = journal_record(buf, size, &pos);
        if (r == REC_TORN) {
            /* Crash mid-append: the record was never acknowledged. */
            if (ftruncate(fd, (off_t)before) != 0 || fdatasync(fd) != 0) {
                fail("cannot truncate torn journal tail");
                free(buf);
                close(fd);
                return false;
            }
            size = before;
            break;
        }
        if (r == REC_CORRUPT) {
            fail("journal is corrupt at offset %zu; refusing to start", before);
            free(buf);
            close(fd);
            return false;
        }
    }
    free(buf);
    g_store.journal_fd = fd;
    g_store.journal_size = (off_t)size;
    return true;
}

/* ------------------------------------------------------------- connections */

enum { CONN_FREE = 0, CONN_READ, CONN_WAIT, CONN_WRITE, CONN_LINGER };
enum { ROLE_NONE = 0, ROLE_ADMIN = 1, ROLE_AGENT = 2 };

typedef struct {
    int fd;
    int phase;
    char *in;
    size_t in_len;
    size_t head_len;
    size_t content_length;
    int64_t deadline;
    size_t drained;
    char *out;
    size_t out_len;
    size_t out_off;
    uint64_t wait_seq;
} conn;

static conn g_conns[MAX_CONNECTIONS];
static uint64_t g_wait_seq = 0;
static char g_admin_token[TOKEN_MAX + 1];
static size_t g_admin_len;
static char g_agent_token[TOKEN_MAX + 1];
static size_t g_agent_len;

static const char *reason_phrase(int status)
{
    switch (status) {
    case 200:
        return "OK";
    case 201:
        return "Created";
    case 204:
        return "No Content";
    case 400:
        return "Bad Request";
    case 401:
        return "Unauthorized";
    case 403:
        return "Forbidden";
    case 404:
        return "Not Found";
    case 405:
        return "Method Not Allowed";
    case 408:
        return "Request Timeout";
    case 409:
        return "Conflict";
    case 411:
        return "Length Required";
    case 413:
        return "Content Too Large";
    case 415:
        return "Unsupported Media Type";
    case 422:
        return "Unprocessable Content";
    case 429:
        return "Too Many Requests";
    case 431:
        return "Request Header Fields Too Large";
    case 503:
        return "Service Unavailable";
    case 507:
        return "Insufficient Storage";
    default:
        return "Internal Server Error";
    }
}

static void conn_close(conn *c)
{
    if (c->fd >= 0) {
        close(c->fd);
    }
    free(c->in);
    free(c->out);
    memset(c, 0, sizeof(*c));
    c->fd = -1;
    c->phase = CONN_FREE;
}

/* Takes ownership of `body` (may be NULL). */
static void respond_owned(conn *c, int status, char *body, size_t body_len)
{
    char head[256];
    int n;
    if (status == 204) {
        n = snprintf(head, sizeof(head),
                     "HTTP/1.1 204 No Content\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n");
    } else {
        n = snprintf(head, sizeof(head),
                     "HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %zu\r\n"
                     "Cache-Control: no-store\r\nConnection: close\r\n\r\n",
                     status, reason_phrase(status), body_len);
    }
    if (n < 0 || (size_t)n >= sizeof(head)) {
        free(body);
        conn_close(c);
        return;
    }
    size_t total = (size_t)n + (status == 204 ? 0 : body_len);
    char *out = malloc(total);
    if (out == NULL) {
        free(body);
        conn_close(c);
        return;
    }
    memcpy(out, head, (size_t)n);
    if (status != 204 && body_len > 0) {
        memcpy(out + n, body, body_len);
    }
    free(body);
    free(c->out);
    c->out = out;
    c->out_len = total;
    c->out_off = 0;
    c->phase = CONN_WRITE;
    c->deadline = now_ms() + IDLE_TIMEOUT_MS;
}

static void respond_json(conn *c, int status, const char *json)
{
    size_t len = strlen(json);
    char *body = malloc(len + 1);
    if (body == NULL) {
        conn_close(c);
        return;
    }
    memcpy(body, json, len + 1);
    respond_owned(c, status, body, len);
}

static void respond_error(conn *c, int status, const char *code)
{
    char buf[96];
    int n = snprintf(buf, sizeof(buf), "{\"error\":\"%s\"}", code);
    if (n < 0 || (size_t)n >= sizeof(buf)) {
        conn_close(c);
        return;
    }
    respond_json(c, status, buf);
}

/* ---------------------------------------------------------------- handlers */

static void requeue_expired_leases(int64_t now)
{
    for (size_t i = g_store.queue_scan; i < g_store.count; i++) {
        command *cmd = &g_store.items[i];
        if (cmd->state == STATE_DELIVERED && cmd->lease_until <= now) {
            cmd->state = STATE_QUEUED;
        }
    }
}

static size_t queued_available(void)
{
    size_t n = 0;
    while (g_store.queue_scan < g_store.count &&
           (g_store.items[g_store.queue_scan].state == STATE_DONE ||
            g_store.items[g_store.queue_scan].state == STATE_FAILED)) {
        g_store.queue_scan++;
    }
    for (size_t i = g_store.queue_scan; i < g_store.count; i++) {
        if (g_store.items[i].state == STATE_QUEUED) {
            n++;
        }
    }
    return n;
}

/* Delivers up to POLL_BATCH queued commands to an agent poll. */
static void deliver(conn *c)
{
    int64_t now = now_ms();
    size_t cap = 16;
    size_t picked[POLL_BATCH];
    size_t npicked = 0;
    for (size_t i = g_store.queue_scan; i < g_store.count && npicked < POLL_BATCH; i++) {
        command *cmd = &g_store.items[i];
        if (cmd->state == STATE_QUEUED) {
            picked[npicked++] = i;
            cap += cmd->id_len + strlen(cmd->kind) + cmd->payload_len + 40;
        }
    }
    char *body = malloc(cap);
    if (body == NULL) {
        respond_error(c, 503, "unavailable");
        return;
    }
    size_t n = 0;
    memcpy(body, "{\"commands\":[", 13);
    n = 13;
    for (size_t k = 0; k < npicked; k++) {
        command *cmd = &g_store.items[picked[k]];
        int w = snprintf(body + n, cap - n, "%s{\"id\":\"%s\",\"kind\":\"%s\",\"payload\":", k == 0 ? "" : ",",
                         cmd->id, cmd->kind);
        if (w < 0 || (size_t)w >= cap - n) {
            free(body);
            respond_error(c, 503, "unavailable");
            return;
        }
        n += (size_t)w;
        memcpy(body + n, cmd->payload, cmd->payload_len);
        n += cmd->payload_len;
        body[n++] = '}';
        cmd->state = STATE_DELIVERED;
        cmd->lease_until = now + LEASE_MS;
    }
    body[n++] = ']';
    body[n++] = '}';
    respond_owned(c, 200, body, n);
}

static void wake_waiters(void)
{
    for (;;) {
        if (queued_available() == 0) {
            return;
        }
        conn *oldest = NULL;
        for (size_t i = 0; i < MAX_CONNECTIONS; i++) {
            conn *w = &g_conns[i];
            if (w->phase == CONN_WAIT && (oldest == NULL || w->wait_seq < oldest->wait_seq)) {
                oldest = w;
            }
        }
        if (oldest == NULL) {
            return;
        }
        deliver(oldest);
    }
}

static void handle_create(conn *c, const char *body, size_t len)
{
    json_field fields[3] = {
        {"id", 's', NULL, 0, false},
        {"kind", 's', NULL, 0, false},
        {"payload", 'o', NULL, 0, false},
    };
    int r = json_object_fields(body, len, fields, 3);
    if (r == JSON_SYNTAX) {
        respond_error(c, 400, "invalid_json");
        return;
    }
    if (r != JSON_OK || !valid_id(fields[0].start, fields[0].len) || !valid_kind(fields[1].start, fields[1].len)) {
        respond_error(c, 422, "invalid_command");
        return;
    }
    if (store_find(fields[0].start, fields[0].len) != NULL) {
        respond_error(c, 409, "duplicate_id");
        return;
    }
    if (g_store.pending >= MAX_PENDING) {
        respond_error(c, 429, "queue_full");
        return;
    }
    if (g_store.count >= MAX_COMMANDS || g_store.payload_bytes + fields[2].len > PAYLOAD_TOTAL_MAX) {
        respond_error(c, 507, "store_full");
        return;
    }
    const char *rec[3] = {fields[0].start, fields[1].start, fields[2].start};
    size_t lens[3] = {fields[0].len, fields[1].len, fields[2].len};
    if (!journal_append('C', rec, lens, 3)) {
        respond_error(c, 503, "storage_unavailable");
        return;
    }
    if (!store_insert(fields[0].start, fields[0].len, fields[1].start, fields[1].len, fields[2].start,
                      fields[2].len)) {
        /* The record is durable; memory must match it or the process is wrong. */
        fail("out of memory after durable append; exiting for replay");
        exit(3);
    }
    char out[ID_MAX + 48];
    snprintf(out, sizeof(out), "{\"id\":\"%.*s\",\"state\":\"queued\"}", (int)fields[0].len, fields[0].start);
    respond_json(c, 201, out);
    wake_waiters();
}

static void handle_ack(conn *c, const char *id, size_t id_len, const char *body, size_t len)
{
    json_field fields[1] = {{"status", 's', NULL, 0, false}};
    int r = json_object_fields(body, len, fields, 1);
    if (r == JSON_SYNTAX) {
        respond_error(c, 400, "invalid_json");
        return;
    }
    int state;
    if (r != JSON_OK) {
        respond_error(c, 422, "invalid_ack");
        return;
    }
    if (fields[0].len == 4 && memcmp(fields[0].start, "done", 4) == 0) {
        state = STATE_DONE;
    } else if (fields[0].len == 6 && memcmp(fields[0].start, "failed", 6) == 0) {
        state = STATE_FAILED;
    } else {
        respond_error(c, 422, "invalid_ack");
        return;
    }
    command *cmd = valid_id(id, id_len) ? store_find(id, id_len) : NULL;
    if (cmd == NULL) {
        respond_error(c, 404, "not_found");
        return;
    }
    if (cmd->state == STATE_DONE || cmd->state == STATE_FAILED) {
        respond_error(c, 409, "already_acknowledged");
        return;
    }
    const char *rec[2] = {id, fields[0].start};
    size_t lens[2] = {id_len, fields[0].len};
    if (!journal_append('A', rec, lens, 2)) {
        respond_error(c, 503, "storage_unavailable");
        return;
    }
    store_finish(cmd, state);
    char out[ID_MAX + 48];
    snprintf(out, sizeof(out), "{\"id\":\"%s\",\"state\":\"%s\"}", cmd->id, state_name(state));
    respond_json(c, 200, out);
}

static void handle_status(conn *c, const char *id, size_t id_len)
{
    command *cmd = valid_id(id, id_len) ? store_find(id, id_len) : NULL;
    if (cmd == NULL) {
        respond_error(c, 404, "not_found");
        return;
    }
    char out[ID_MAX + KIND_MAX + 64];
    snprintf(out, sizeof(out), "{\"id\":\"%s\",\"kind\":\"%s\",\"state\":\"%s\"}", cmd->id, cmd->kind,
             state_name(cmd->state));
    respond_json(c, 200, out);
}

static void handle_poll(conn *c, const char *query, size_t qlen)
{
    long timeout = 0;
    if (qlen > 0) {
        static const char key[] = "timeout_ms=";
        size_t klen = sizeof(key) - 1;
        if (qlen <= klen || qlen > klen + 5 || memcmp(query, key, klen) != 0) {
            respond_error(c, 400, "invalid_query");
            return;
        }
        for (size_t i = klen; i < qlen; i++) {
            if (query[i] < '0' || query[i] > '9') {
                respond_error(c, 400, "invalid_query");
                return;
            }
            timeout = timeout * 10 + (query[i] - '0');
        }
        if (timeout > POLL_TIMEOUT_MAX_MS) {
            respond_error(c, 400, "invalid_query");
            return;
        }
    }
    requeue_expired_leases(now_ms());
    if (queued_available() > 0) {
        deliver(c);
        return;
    }
    if (timeout == 0) {
        respond_owned(c, 204, NULL, 0);
        return;
    }
    c->phase = CONN_WAIT;
    c->deadline = now_ms() + timeout;
    c->wait_seq = ++g_wait_seq;
}

static int authenticate(const char *value, size_t len)
{
    static const char prefix[] = "Bearer ";
    size_t plen = sizeof(prefix) - 1;
    if (value == NULL || len <= plen || memcmp(value, prefix, plen) != 0) {
        return ROLE_NONE;
    }
    const char *tok = value + plen;
    size_t tlen = len - plen;
    bool admin = token_equal(tok, tlen, g_admin_token, g_admin_len);
    bool agent = token_equal(tok, tlen, g_agent_token, g_agent_len);
    if (admin) {
        return ROLE_ADMIN;
    }
    if (agent) {
        return ROLE_AGENT;
    }
    return ROLE_NONE;
}

typedef struct {
    const char *method;
    size_t method_len;
    const char *target;
    size_t target_len;
    const char *auth;
    size_t auth_len;
    const char *ctype;
    size_t ctype_len;
    bool has_length;
    size_t length;
} request_head;

/* Returns 0 when the head is acceptable, otherwise the HTTP status to send. */
static int parse_head(const char *buf, size_t len, request_head *h)
{
    memset(h, 0, sizeof(*h));
    for (size_t i = 0; i < len; i++) {
        unsigned char ch = (unsigned char)buf[i];
        if (ch == 0 || ch >= 0x7f || (ch == '\n' && (i == 0 || buf[i - 1] != '\r')) ||
            (ch == '\r' && (i + 1 >= len || buf[i + 1] != '\n'))) {
            return 400;
        }
    }
    const char *p = buf;
    const char *end = buf + len;
    const char *eol = memchr(p, '\r', (size_t)(end - p));
    if (eol == NULL) {
        return 400;
    }
    const char *sp1 = memchr(p, ' ', (size_t)(eol - p));
    if (sp1 == NULL) {
        return 400;
    }
    const char *sp2 = memchr(sp1 + 1, ' ', (size_t)(eol - sp1 - 1));
    if (sp2 == NULL) {
        return 400;
    }
    h->method = p;
    h->method_len = (size_t)(sp1 - p);
    h->target = sp1 + 1;
    h->target_len = (size_t)(sp2 - sp1 - 1);
    size_t vlen = (size_t)(eol - sp2 - 1);
    if (!((vlen == 8 && memcmp(sp2 + 1, "HTTP/1.1", 8) == 0) || (vlen == 8 && memcmp(sp2 + 1, "HTTP/1.0", 8) == 0))) {
        return 400;
    }
    if (h->target_len == 0 || h->target_len > TARGET_MAX || h->target[0] != '/') {
        return 400;
    }
    for (size_t i = 0; i < h->target_len; i++) {
        if ((unsigned char)h->target[i] <= 0x20) {
            return 400;
        }
    }
    p = eol + 2;
    int count = 0;
    while (p < end) {
        eol = memchr(p, '\r', (size_t)(end - p));
        if (eol == NULL) {
            return 400;
        }
        if (eol == p) {
            break; /* blank line */
        }
        if (++count > MAX_HEADERS) {
            return 431;
        }
        if (*p == ' ' || *p == '\t') {
            return 400; /* obsolete line folding */
        }
        const char *colon = memchr(p, ':', (size_t)(eol - p));
        if (colon == NULL || colon == p) {
            return 400;
        }
        for (const char *q = p; q < colon; q++) {
            if (*q == ' ' || *q == '\t') {
                return 400;
            }
        }
        size_t nlen = (size_t)(colon - p);
        const char *v = colon + 1;
        while (v < eol && (*v == ' ' || *v == '\t')) {
            v++;
        }
        const char *ve = eol;
        while (ve > v && (ve[-1] == ' ' || ve[-1] == '\t')) {
            ve--;
        }
        size_t vl = (size_t)(ve - v);
        if (ascii_ieq(p, nlen, "transfer-encoding")) {
            return 400; /* only Content-Length framing; rejects CL.TE smuggling */
        } else if (ascii_ieq(p, nlen, "content-length")) {
            if (h->has_length || vl == 0 || vl > 9) {
                return 400;
            }
            size_t value = 0;
            for (size_t i = 0; i < vl; i++) {
                if (v[i] < '0' || v[i] > '9') {
                    return 400;
                }
                value = value * 10 + (size_t)(v[i] - '0');
            }
            h->has_length = true;
            h->length = value;
        } else if (ascii_ieq(p, nlen, "authorization")) {
            if (h->auth != NULL) {
                return 400;
            }
            h->auth = v;
            h->auth_len = vl;
        } else if (ascii_ieq(p, nlen, "content-type")) {
            if (h->ctype != NULL) {
                return 400;
            }
            h->ctype = v;
            h->ctype_len = vl;
        }
        p = eol + 2;
    }
    return 0;
}

static bool is_json_type(const char *v, size_t len)
{
    size_t n = 0;
    while (n < len && v[n] != ';' && v[n] != ' ' && v[n] != '\t') {
        n++;
    }
    return ascii_ieq(v, n, "application/json");
}

static void dispatch(conn *c, const request_head *h)
{
    const char *body = c->in + c->head_len;
    size_t body_len = c->content_length;
    int role = authenticate(h->auth, h->auth_len);
    if (role == ROLE_NONE) {
        respond_error(c, 401, "unauthorized");
        return;
    }
    bool is_get = h->method_len == 3 && memcmp(h->method, "GET", 3) == 0;
    bool is_post = h->method_len == 4 && memcmp(h->method, "POST", 4) == 0;
    const char *t = h->target;
    size_t tl = h->target_len;
    const char *query = memchr(t, '?', tl);
    size_t path_len = query != NULL ? (size_t)(query - t) : tl;
    size_t qlen = query != NULL ? tl - path_len - 1 : 0;
    if (query != NULL) {
        query++;
    }
    static const char base[] = "/v1/commands";
    size_t blen = sizeof(base) - 1;
    if (path_len < blen || memcmp(t, base, blen) != 0) {
        respond_error(c, 404, "not_found");
        return;
    }
    const char *rest = t + blen;
    size_t rest_len = path_len - blen;
    if (rest_len == 0) {
        if (!is_post) {
            respond_error(c, 405, "method_not_allowed");
            return;
        }
        if (role != ROLE_ADMIN) {
            respond_error(c, 403, "forbidden");
            return;
        }
        if (h->ctype == NULL || !is_json_type(h->ctype, h->ctype_len)) {
            respond_error(c, 415, "unsupported_media_type");
            return;
        }
        handle_create(c, body, body_len);
        return;
    }
    if (rest[0] != '/') {
        respond_error(c, 404, "not_found");
        return;
    }
    rest++;
    rest_len--;
    if (rest_len == 4 && memcmp(rest, "poll", 4) == 0) {
        if (!is_get) {
            respond_error(c, 405, "method_not_allowed");
            return;
        }
        if (role != ROLE_AGENT) {
            respond_error(c, 403, "forbidden");
            return;
        }
        handle_poll(c, query, qlen);
        return;
    }
    if (query != NULL) {
        respond_error(c, 400, "invalid_query");
        return;
    }
    static const char ack[] = "/ack";
    size_t alen = sizeof(ack) - 1;
    if (rest_len > alen && memcmp(rest + rest_len - alen, ack, alen) == 0) {
        if (!is_post) {
            respond_error(c, 405, "method_not_allowed");
            return;
        }
        if (role != ROLE_AGENT) {
            respond_error(c, 403, "forbidden");
            return;
        }
        if (h->ctype == NULL || !is_json_type(h->ctype, h->ctype_len)) {
            respond_error(c, 415, "unsupported_media_type");
            return;
        }
        handle_ack(c, rest, rest_len - alen, body, body_len);
        return;
    }
    if (!is_get) {
        respond_error(c, 405, "method_not_allowed");
        return;
    }
    if (role != ROLE_ADMIN) {
        respond_error(c, 403, "forbidden");
        return;
    }
    handle_status(c, rest, rest_len);
}

/* Reads from the socket and advances the request state machine. */
static void conn_on_readable(conn *c)
{
    if (c->phase == CONN_LINGER || c->phase == CONN_WAIT) {
        char scratch[4096];
        ssize_t n = read(c->fd, scratch, sizeof(scratch));
        if (n == 0 || (n < 0 && errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)) {
            conn_close(c);
            return;
        }
        if (n > 0) {
            c->drained += (size_t)n;
            if (c->phase == CONN_LINGER && c->drained > LINGER_DRAIN_MAX) {
                conn_close(c);
            }
            /* A waiting poll carries no body; extra bytes are ignored. */
        }
        return;
    }
    if (c->phase != CONN_READ) {
        return;
    }
    size_t limit = HEADER_MAX + BODY_MAX;
    if (c->in_len >= limit) {
        respond_error(c, 413, "too_large");
        return;
    }
    ssize_t n = read(c->fd, c->in + c->in_len, limit - c->in_len);
    if (n < 0) {
        if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) {
            return;
        }
        conn_close(c);
        return;
    }
    if (n == 0) {
        conn_close(c);
        return;
    }
    c->in_len += (size_t)n;
    if (c->head_len == 0) {
        size_t scan = c->in_len < HEADER_MAX ? c->in_len : HEADER_MAX;
        const char *found = NULL;
        for (size_t i = 3; i < scan; i++) {
            if (c->in[i - 3] == '\r' && c->in[i - 2] == '\n' && c->in[i - 1] == '\r' && c->in[i] == '\n') {
                found = c->in + i + 1;
                break;
            }
        }
        if (found == NULL) {
            if (c->in_len >= HEADER_MAX) {
                respond_error(c, 431, "header_too_large");
            }
            return;
        }
        c->head_len = (size_t)(found - c->in);
        request_head h;
        int status = parse_head(c->in, c->head_len - 2, &h);
        if (status != 0) {
            respond_error(c, status, status == 431 ? "header_too_large" : "bad_request");
            return;
        }
        bool is_post = h.method_len == 4 && memcmp(h.method, "POST", 4) == 0;
        if (is_post && !h.has_length) {
            respond_error(c, 411, "length_required");
            return;
        }
        c->content_length = h.has_length ? h.length : 0;
        if (c->content_length > BODY_MAX) {
            respond_error(c, 413, "too_large");
            return;
        }
    }
    if (c->in_len - c->head_len < c->content_length) {
        return;
    }
    request_head h;
    if (parse_head(c->in, c->head_len - 2, &h) != 0) {
        respond_error(c, 400, "bad_request");
        return;
    }
    dispatch(c, &h);
}

static void conn_on_writable(conn *c)
{
    while (c->out_off < c->out_len) {
        ssize_t n = write(c->fd, c->out + c->out_off, c->out_len - c->out_off);
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                return;
            }
            conn_close(c);
            return;
        }
        c->out_off += (size_t)n;
    }
    /* Half-close and drain unread request bytes so the peer gets our response
     * instead of a reset. */
    shutdown(c->fd, SHUT_WR);
    c->phase = CONN_LINGER;
    c->deadline = now_ms() + LINGER_TIMEOUT_MS;
}

static void accept_ready(int lfd)
{
    for (;;) {
        int fd = accept(lfd, NULL, NULL);
        if (fd < 0) {
            return;
        }
        conn *slot = NULL;
        for (size_t i = 0; i < MAX_CONNECTIONS; i++) {
            if (g_conns[i].phase == CONN_FREE) {
                slot = &g_conns[i];
                break;
            }
        }
        int flags = fcntl(fd, F_GETFL);
        if (slot == NULL || flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) != 0 ||
            fcntl(fd, F_SETFD, FD_CLOEXEC) != 0) {
            close(fd);
            continue;
        }
        slot->in = malloc(HEADER_MAX + BODY_MAX);
        if (slot->in == NULL) {
            close(fd);
            continue;
        }
        slot->fd = fd;
        slot->phase = CONN_READ;
        slot->deadline = now_ms() + IDLE_TIMEOUT_MS;
    }
}

static void on_deadline(conn *c)
{
    switch (c->phase) {
    case CONN_WAIT:
        respond_owned(c, 204, NULL, 0);
        break;
    case CONN_READ:
        respond_error(c, 408, "timeout");
        break;
    default:
        conn_close(c);
        break;
    }
}

/* --------------------------------------------------------------- startup */

static bool read_token(const char *path, char *out, size_t *out_len)
{
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) {
        fail("cannot open token file %s", path);
        return false;
    }
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || (st.st_mode & 077) != 0 || st.st_size > TOKEN_MAX) {
        fail("token file %s must be a private regular file (mode 0600)", path);
        close(fd);
        return false;
    }
    size_t got = 0;
    while (got < (size_t)st.st_size) {
        ssize_t n = read(fd, out + got, (size_t)st.st_size - got);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            break;
        }
        got += (size_t)n;
    }
    close(fd);
    while (got > 0 && (out[got - 1] == '\n' || out[got - 1] == '\r')) {
        got--;
    }
    out[got] = '\0';
    if (got < TOKEN_MIN) {
        fail("token in %s is shorter than %d bytes", path, TOKEN_MIN);
        return false;
    }
    for (size_t i = 0; i < got; i++) {
        if (out[i] < 0x21 || out[i] > 0x7e) {
            fail("token in %s must be printable ASCII without spaces", path);
            return false;
        }
    }
    *out_len = got;
    return true;
}

static int open_state_dir(const char *path)
{
    if (mkdir(path, 0700) != 0 && errno != EEXIST) {
        fail("cannot create state directory: %s", strerror(errno));
        return -1;
    }
    struct stat st;
    if (lstat(path, &st) != 0 || !S_ISDIR(st.st_mode)) {
        fail("state directory must be a real directory");
        return -1;
    }
    if ((st.st_mode & 077) != 0 || st.st_uid != geteuid()) {
        fail("state directory must be owned by this user with mode 0700");
        return -1;
    }
    int fd = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) {
        fail("cannot open state directory: %s", strerror(errno));
    }
    return fd;
}

static bool lock_state_dir(int dirfd)
{
    int fd = openat(dirfd, "lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) {
        fail("cannot open lock file: %s", strerror(errno));
        return false;
    }
    struct flock fl;
    memset(&fl, 0, sizeof(fl));
    fl.l_type = F_WRLCK;
    fl.l_whence = SEEK_SET;
    if (fcntl(fd, F_SETLK, &fl) != 0) {
        fail("state directory is in use by another server");
        close(fd);
        return false;
    }
    return true; /* fd intentionally kept open for the process lifetime */
}

static int open_listener(const char *spec, int *port_out)
{
    static const char prefix[] = "127.0.0.1:";
    size_t plen = sizeof(prefix) - 1;
    if (strncmp(spec, prefix, plen) != 0) {
        fail("--listen must be 127.0.0.1:<port>; the server is loopback-only");
        return -1;
    }
    const char *ps = spec + plen;
    size_t pl = strlen(ps);
    if (pl == 0 || pl > 5) {
        fail("invalid listen port");
        return -1;
    }
    long port = 0;
    for (size_t i = 0; i < pl; i++) {
        if (ps[i] < '0' || ps[i] > '9') {
            fail("invalid listen port");
            return -1;
        }
        port = port * 10 + (ps[i] - '0');
    }
    if (port > 65535) {
        fail("invalid listen port");
        return -1;
    }
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        fail("socket: %s", strerror(errno));
        return -1;
    }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0 || listen(fd, 128) != 0) {
        fail("bind/listen: %s", strerror(errno));
        close(fd);
        return -1;
    }
    socklen_t alen = sizeof(addr);
    if (getsockname(fd, (struct sockaddr *)&addr, &alen) != 0) {
        close(fd);
        return -1;
    }
    int flags = fcntl(fd, F_GETFL);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) != 0 || fcntl(fd, F_SETFD, FD_CLOEXEC) != 0) {
        close(fd);
        return -1;
    }
    *port_out = ntohs(addr.sin_port);
    return fd;
}

static bool write_ready_file(const char *path, int port)
{
    char tmp[PATH_MAX];
    int n = snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    if (n < 0 || (size_t)n >= sizeof(tmp)) {
        fail("ready file path too long");
        return false;
    }
    unlink(tmp);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) {
        fail("cannot create ready file: %s", strerror(errno));
        return false;
    }
    char buf[64];
    n = snprintf(buf, sizeof(buf), "{\"url\":\"http://127.0.0.1:%d\"}\n", port);
    bool ok = n > 0 && (size_t)n < sizeof(buf) && write_all(fd, buf, (size_t)n) && fsync(fd) == 0;
    close(fd);
    if (!ok || rename(tmp, path) != 0) {
        unlink(tmp);
        fail("cannot publish ready file");
        return false;
    }
    return true;
}

static void usage(void)
{
    fputs("usage: alice-lan-c17 --listen 127.0.0.1:<port> --state-dir DIR --ready-file FILE\n"
          "                     --admin-token-file FILE --agent-token-file FILE\n",
          stderr);
}

int main(int argc, char **argv)
{
    const char *listen_spec = NULL;
    const char *state_dir = NULL;
    const char *ready_file = NULL;
    const char *admin_file = NULL;
    const char *agent_file = NULL;
    for (int i = 1; i < argc; i++) {
        const char **slot = NULL;
        if (strcmp(argv[i], "--listen") == 0) {
            slot = &listen_spec;
        } else if (strcmp(argv[i], "--state-dir") == 0) {
            slot = &state_dir;
        } else if (strcmp(argv[i], "--ready-file") == 0) {
            slot = &ready_file;
        } else if (strcmp(argv[i], "--admin-token-file") == 0) {
            slot = &admin_file;
        } else if (strcmp(argv[i], "--agent-token-file") == 0) {
            slot = &agent_file;
        } else if (strcmp(argv[i], "--help") == 0) {
            usage();
            return 0;
        }
        if (slot == NULL || i + 1 >= argc || *slot != NULL) {
            usage();
            return 2;
        }
        *slot = argv[++i];
    }
    if (listen_spec == NULL || state_dir == NULL || ready_file == NULL || admin_file == NULL || agent_file == NULL) {
        usage();
        return 2;
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_stop_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    if (!read_token(admin_file, g_admin_token, &g_admin_len) || !read_token(agent_file, g_agent_token, &g_agent_len)) {
        return 2;
    }
    if (token_equal(g_admin_token, g_admin_len, g_agent_token, g_agent_len)) {
        fail("admin and agent tokens must differ");
        return 2;
    }
    int dirfd = open_state_dir(state_dir);
    if (dirfd < 0 || !lock_state_dir(dirfd)) {
        return 2;
    }
    g_store.slots = calloc(HASH_SLOTS, sizeof(uint32_t));
    if (g_store.slots == NULL || !journal_load(dirfd)) {
        return 3;
    }
    int port = 0;
    int lfd = open_listener(listen_spec, &port);
    if (lfd < 0) {
        return 2;
    }
    for (size_t i = 0; i < MAX_CONNECTIONS; i++) {
        g_conns[i].fd = -1;
    }
    if (!write_ready_file(ready_file, port)) {
        return 2;
    }

    struct pollfd fds[MAX_CONNECTIONS + 1];
    conn *owners[MAX_CONNECTIONS + 1];
    while (!stop_requested) {
        int64_t now = now_ms();
        int64_t next = now + 1000;
        nfds_t nfds = 0;
        fds[nfds].fd = lfd;
        fds[nfds].events = POLLIN;
        fds[nfds].revents = 0;
        owners[nfds++] = NULL;
        for (size_t i = 0; i < MAX_CONNECTIONS; i++) {
            conn *c = &g_conns[i];
            if (c->phase == CONN_FREE) {
                continue;
            }
            if (c->deadline < next) {
                next = c->deadline;
            }
            fds[nfds].fd = c->fd;
            fds[nfds].events = c->phase == CONN_WRITE ? POLLOUT : POLLIN;
            fds[nfds].revents = 0;
            owners[nfds++] = c;
        }
        int wait = next > now ? (int)(next - now) : 0;
        int ready = poll(fds, nfds, wait);
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            fail("poll: %s", strerror(errno));
            return 3;
        }
        for (nfds_t i = 1; i < nfds; i++) {
            conn *c = owners[i];
            short re = fds[i].revents;
            if (re == 0 || c->phase == CONN_FREE || c->fd != fds[i].fd) {
                continue;
            }
            if (re & (POLLERR | POLLNVAL)) {
                conn_close(c);
            } else if (c->phase == CONN_WRITE && (re & POLLOUT)) {
                conn_on_writable(c);
            } else if (re & (POLLIN | POLLHUP)) {
                conn_on_readable(c);
            }
            if (c->phase == CONN_WRITE) {
                conn_on_writable(c);
            }
        }
        if (fds[0].revents & POLLIN) {
            accept_ready(lfd);
        }
        now = now_ms();
        for (size_t i = 0; i < MAX_CONNECTIONS; i++) {
            conn *c = &g_conns[i];
            if (c->phase != CONN_FREE && c->deadline <= now) {
                on_deadline(c);
                if (c->phase == CONN_WRITE) {
                    conn_on_writable(c);
                }
            }
        }
    }

    for (size_t i = 0; i < MAX_CONNECTIONS; i++) {
        if (g_conns[i].phase != CONN_FREE) {
            conn_close(&g_conns[i]);
        }
    }
    close(lfd);
    unlink(ready_file);
    fdatasync(g_store.journal_fd);
    close(g_store.journal_fd);
    for (size_t i = 0; i < g_store.count; i++) {
        free(g_store.items[i].payload);
    }
    free(g_store.items);
    free(g_store.slots);
    return 0;
}
