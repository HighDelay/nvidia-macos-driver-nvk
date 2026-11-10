/* SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 */
#ifndef NVMTL_FD_TOKEN_H
#define NVMTL_FD_TOKEN_H
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>

/* WebKit denies both a fresh null open and pipe(), but permits dup() of its
 * already-open, read-only null stdin. Validate the device and access mode,
 * then own a separately closed duplicate. Never read/consume stdin or use a
 * terminal, regular file, pipe, socket, writable null descriptor or other device.
 * F_DUPFD_CLOEXEC is separately denied there, so set CLOEXEC on our dup only. */
static int nvmtl_null_stdin_token(int flags)
{
    const int saved = errno;
    struct stat expected, inherited;
    if (lstat("/dev/null", &expected) || !S_ISCHR(expected.st_mode) ||
        expected.st_uid != 0) { errno = saved; return -1; }
    int fd = dup(STDIN_FILENO);
    if (fd < 0) return fd;
    /* Validate the owned duplicate, so a concurrent stdin replacement cannot
     * substitute a different object between verification and duplication. */
    const int mode = fcntl(fd, F_GETFL);
    if (mode < 0 || (mode & O_ACCMODE) != O_RDONLY ||
        fstat(fd, &inherited) || !S_ISCHR(inherited.st_mode) ||
        expected.st_dev != inherited.st_dev || expected.st_rdev != inherited.st_rdev) {
        close(fd); errno = saved; return -1;
    }
    if ((flags & O_CLOEXEC) && fcntl(fd, F_SETFD, FD_CLOEXEC) < 0) {
        const int failure = errno; close(fd); errno = failure; return -1;
    }
    return fd;
}

/* The RM bridge uses a read-only null descriptor as an owned handle number.
 * If the host sandbox forbids that path, an anonymous pipe at EOF has the
 * same read-only behavior. A verified null-stdin duplicate is the final option
 * if the sandbox also denies pipe(). Other paths, access modes and failures
 * retain normal semantics. Every returned descriptor is independently owned. */
static int nvmtl_icd_open(const char *path, int flags, ...)
{
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list args; va_start(args, flags); mode = (mode_t)va_arg(args, int); va_end(args);
    }
    int fd = open(path, flags, mode);
    if (fd >= 0 || !path || strcmp(path, "/dev/null") ||
        (flags & ~O_CLOEXEC) != O_RDONLY ||
        (errno != EACCES && errno != EPERM)) return fd;
    int ends[2];
    if (pipe(ends)) {
        if (errno == EACCES || errno == EPERM) return nvmtl_null_stdin_token(flags);
        return -1;
    }
    close(ends[1]);
    if ((flags & O_CLOEXEC) && fcntl(ends[0], F_SETFD, FD_CLOEXEC) < 0) {
        int error = errno; close(ends[0]); errno = error; return -1;
    }
    return ends[0];
}
#endif
