/* Double-click launcher. Starts python\python.exe and opens the browser.
   If this app is already running, it opens that window instead. */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <string.h>

static void cut_filename(char *path) {
    char *slash = strrchr(path, '\\');
    if (slash) *slash = '\0';
}

static void join(char *dest, size_t size, const char *dir, const char *rest) {
    snprintf(dest, size, "%s\\%s", dir, rest);
}

static int health_on(int port) {
    SOCKET sock;
    struct sockaddr_in addr;
    char request[] = "GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    char buf[600];
    int got = 0;
    DWORD timeout = 400;
    sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock == INVALID_SOCKET) return 0;
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (char *)&timeout, sizeof timeout);
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, (char *)&timeout, sizeof timeout);
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons((unsigned short)port);
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(sock, (struct sockaddr *)&addr, sizeof addr) != 0) {
        closesocket(sock);
        return 0;
    }
    send(sock, request, (int)strlen(request), 0);
    while (got < (int)sizeof buf - 1) {
        int n = recv(sock, buf + got, (int)sizeof buf - 1 - got, 0);
        if (n <= 0) break;
        got += n;
    }
    buf[got] = '\0';
    closesocket(sock);
    return strstr(buf, "\"ok\"") != NULL;
}

static int find_running(void) {
    int port;
    for (port = 8765; port <= 8784; port++) {
        if (health_on(port)) return port;
    }
    return 0;
}

static int file_exists(const char *path) {
    DWORD attr = GetFileAttributesA(path);
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

static void open_port(int port, int https) {
    char url[80];
    snprintf(url, sizeof url, "%s://127.0.0.1:%d", https ? "https" : "http", port);
    ShellExecuteA(NULL, "open", url, NULL, NULL, SW_SHOWNORMAL);
}

static int read_port_file(const char *dir) {
    char path[MAX_PATH];
    FILE *file;
    int port = 0;
    snprintf(path, sizeof path, "%s\\data\\port.txt", dir);
    file = fopen(path, "r");
    if (!file) return 0;
    if (fscanf(file, "%d", &port) != 1) port = 0;
    fclose(file);
    if (port < 1 || port > 65535) return 0;
    return port;
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show) {
    char dir[MAX_PATH];
    char python[MAX_PATH];
    char tess[MAX_PATH];
    char prefix[MAX_PATH];
    char apppath[MAX_PATH];
    char portfile[MAX_PATH];
    char cmdline[1200];
    char flagpath[MAX_PATH];
    char certpath[MAX_PATH];
    WSADATA wsa;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    int demo;
    int running;
    int waits;
    int port;
    (void)inst;
    (void)prev;
    (void)show;

    if (!GetModuleFileNameA(NULL, dir, MAX_PATH)) return 1;
    cut_filename(dir);
    snprintf(certpath, sizeof certpath, "%s\\data\\certs\\cert.pem", dir);
    WSAStartup(MAKEWORD(2, 2), &wsa);
    running = find_running();
    if (running) {
        open_port(running, file_exists(certpath));
        return 0;
    }

    join(python, sizeof python, dir, "python\\python.exe");
    join(tess, sizeof tess, dir, "tesseract\\tesseract.exe");
    snprintf(prefix, sizeof prefix, "%s\\tesseract\\tessdata\\", dir);
    join(apppath, sizeof apppath, dir, "app");
    snprintf(portfile, sizeof portfile, "%s\\data\\port.txt", dir);
    DeleteFileA(portfile);
    SetEnvironmentVariableA("TESSERACT_CMD", tess);
    SetEnvironmentVariableA("TESSDATA_PREFIX", prefix);
    SetEnvironmentVariableA("PYTHONPATH", apppath);
    SetEnvironmentVariableA("NARELA_HOME", dir);
    SetEnvironmentVariableA("PYTHONDONTWRITEBYTECODE", "1");

    demo = cmd && strstr(cmd, "--demo");
    snprintf(flagpath, sizeof flagpath, "%s\\phone-access.on", dir);
    snprintf(
        cmdline,
        sizeof cmdline,
        "\"%s\" -m ownerapp%s%s%s",
        python,
        demo ? " --demo" : "",
        file_exists(flagpath) ? " --phone" : "",
        file_exists(certpath) ? " --https" : ""
    );
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    if (!CreateProcessA(NULL, cmdline, NULL, NULL, FALSE, CREATE_NEW_CONSOLE, NULL, dir, &si, &pi)) {
        MessageBoxA(NULL, "Could not start python\\python.exe in this folder.", "Bill Rent Manager", MB_OK | MB_ICONERROR);
        return 1;
    }
    port = 0;
    for (waits = 0; waits < 40 && !port; waits++) {
        Sleep(500);
        port = read_port_file(dir);
    }
    if (!port) port = 8765;
    open_port(port, file_exists(certpath));
    WaitForSingleObject(pi.hProcess, INFINITE);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return 0;
}
