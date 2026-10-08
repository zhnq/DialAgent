// Post Codex's current macOS app-scoped Voice shortcut (Control+Shift+V).
#import <AppKit/AppKit.h>
#include <ApplicationServices/ApplicationServices.h>
#include <CoreFoundation/CoreFoundation.h>
#include <errno.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

static const char *socket_path = "/private/tmp/dialagent-codex-voice.sock";
static const char *status_path = "/private/tmp/dialagent-codex-voice.status";

static bool accessibility_trusted(bool prompt) {
    const void *keys[] = {kAXTrustedCheckOptionPrompt};
    const void *values[] = {prompt ? kCFBooleanTrue : kCFBooleanFalse};
    CFDictionaryRef options = CFDictionaryCreate(
        kCFAllocatorDefault, keys, values, 1,
        &kCFCopyStringDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks);
    if (options == NULL) return false;
    bool trusted = AXIsProcessTrustedWithOptions(options);
    CFRelease(options);
    return trusted;
}

static int post_voice_shortcut(void) {
    // kVK_ANSI_V is 9 on the macOS virtual-key map. Keeping the value local
    // avoids a dependency on the deprecated Carbon headers.
    const CGKeyCode voice_key = 9;
    const CGEventFlags flags = kCGEventFlagMaskControl | kCGEventFlagMaskShift;
    CGEventSourceRef source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState);
    if (source == NULL) return 0;
    CGEventSourceSetLocalEventsSuppressionInterval(source, 0.0);
    CGEventRef down = CGEventCreateKeyboardEvent(source, voice_key, true);
    CGEventRef up = CGEventCreateKeyboardEvent(source, voice_key, false);
    if (down == NULL || up == NULL) {
        if (down != NULL) CFRelease(down);
        if (up != NULL) CFRelease(up);
        CFRelease(source);
        return 0;
    }
    CGEventSetFlags(down, flags);
    CGEventSetFlags(up, flags);
    CGEventPost(kCGSessionEventTap, down);
    usleep(35000);
    CGEventPost(kCGSessionEventTap, up);
    CFRelease(down);
    CFRelease(up);
    CFRelease(source);
    return 1;
}

static bool activate_codex(void) {
    @autoreleasepool {
        NSArray<NSRunningApplication *> *apps =
            [NSRunningApplication runningApplicationsWithBundleIdentifier:@"com.openai.codex"];
        if (apps.count == 0) return false;
        return [apps.firstObject activateWithOptions:NSApplicationActivateAllWindows];
    }
}

static void write_all(int fd, const char *text) {
    size_t remaining = strlen(text);
    while (remaining > 0) {
        ssize_t count = write(fd, text, remaining);
        if (count <= 0) return;
        text += count;
        remaining -= (size_t)count;
    }
}

static bool server_already_running(void) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return false;
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    snprintf(address.sun_path, sizeof(address.sun_path), "%s", socket_path);
    bool running = connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0;
    close(fd);
    return running;
}

static int serve(void) {
    if (server_already_running()) return 0;
    unlink(socket_path);
    int listener = socket(AF_UNIX, SOCK_STREAM, 0);
    if (listener < 0) return 2;
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    snprintf(address.sun_path, sizeof(address.sun_path), "%s", socket_path);
    if (bind(listener, (struct sockaddr *)&address, sizeof(address)) != 0 ||
        chmod(socket_path, S_IRUSR | S_IWUSR) != 0 || listen(listener, 4) != 0) {
        close(listener);
        unlink(socket_path);
        return 2;
    }
    FILE *status = fopen(status_path, "w");
    if (status != NULL) {
        fprintf(status, "PID\t%d\nACCESSIBILITY\t%s\nHOTKEY\tControl+Shift+V\n",
                getpid(), accessibility_trusted(false) ? "granted" : "denied");
        fclose(status);
    }
    for (;;) {
        int client = accept(listener, NULL, NULL);
        if (client < 0) {
            if (errno == EINTR) continue;
            break;
        }
        char command[64] = {0};
        ssize_t count = read(client, command, sizeof(command) - 1);
        if (count > 0 && strncmp(command, "check", 5) == 0) {
            write_all(client, accessibility_trusted(false)
                       ? "ACCESSIBILITY\tgranted\nHOTKEY\tControl+Shift+V\n"
                       : "ACCESSIBILITY\tdenied\nHOTKEY\tControl+Shift+V\n");
        } else if (count > 0 && strncmp(command, "toggle", 6) == 0) {
            if (!accessibility_trusted(false)) {
                write_all(client, "ERROR\taccessibility-denied\n");
            } else if (!activate_codex()) {
                write_all(client, "ERROR\tcodex-not-running\n");
            } else {
                usleep(350000);
                if (post_voice_shortcut()) write_all(client, "TOGGLE\tposted\n");
                else write_all(client, "ERROR\tevent-post-failed\n");
            }
        } else if (count > 0 && strncmp(command, "quit", 4) == 0) {
            write_all(client, "QUIT\tok\n");
            close(client);
            break;
        } else {
            write_all(client, "ERROR\tunknown-command\n");
        }
        close(client);
    }
    close(listener);
    unlink(socket_path);
    unlink(status_path);
    return 0;
}

static void usage(const char *program) {
    fprintf(stderr, "Usage: %s serve | check [--prompt] | toggle\n", program);
}

int main(int argc, char **argv) {
    if (argc == 1 || (argc == 2 && strcmp(argv[1], "serve") == 0)) {
        return serve();
    }
    bool is_check = argc >= 2 && strcmp(argv[1], "check") == 0;
    bool prompt = argc == 3 && strcmp(argv[2], "--prompt") == 0;
    if (is_check && (argc == 2 || prompt)) {
        bool trusted = accessibility_trusted(prompt);
        printf("ACCESSIBILITY\t%s\n", trusted ? "granted" : "denied");
        printf("HOTKEY\tControl+Shift+V\n");
        return trusted ? 0 : 3;
    }
    if (argc == 2 && strcmp(argv[1], "toggle") == 0) {
        if (!accessibility_trusted(false)) {
            fprintf(stderr, "Accessibility permission is required to send the Codex Voice shortcut.\n");
            return 3;
        }
        if (!post_voice_shortcut()) {
            fprintf(stderr, "Unable to create the Codex Voice keyboard event.\n");
            return 2;
        }
        printf("TOGGLE\tposted\n");
        return 0;
    }
    usage(argv[0]);
    return 2;
}
