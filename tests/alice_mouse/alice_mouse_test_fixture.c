#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Harmless fixture: never opens /dev/uinput or accesses Magisk. */
int main(int argc, char **argv) {
    if (argc < 3) return 64;
    int fd = open(argv[1], O_WRONLY | O_APPEND | O_CREAT, 0600);
    if (fd < 0) return 65;
    dprintf(fd, "%s %ld\n", argv[2], (long)getpid());
    if (fsync(fd) != 0) { close(fd); return 66; }
    close(fd);
    if (argc > 3 && strcmp(argv[3], "crash") == 0) return 41;
    for (;;) pause();
}
