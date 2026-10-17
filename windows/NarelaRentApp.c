/* Double-click launcher for the portable folder.
   Starts python\python.exe and opens the browser. No admin rights. */
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

static int port_open(void) {
    SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    struct sockaddr_in addr;
    int open;
    if (sock == INVALID_SOCKET) return 0;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(8765);
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    open = connect(sock, (struct sockaddr *)&addr, sizeof addr) == 0;
    closesocket(sock);
    return open;
}

static void join(char *dest, size_t size, const char *dir, const char *rest) {
    snprintf(dest, size, "%s\\%s", dir, rest);
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show) {
    char dir[MAX_PATH];
    char python[MAX_PATH];
    char tess[MAX_PATH];
    char prefix[MAX_PATH];
    char apppath[MAX_PATH];
    char cmdline[1200];
    WSADATA wsa;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    int demo;
    (void)inst;
    (void)prev;
    (void)show;

    if (!GetModuleFileNameA(NULL, dir, MAX_PATH)) return 1;
    cut_filename(dir);
    WSAStartup(MAKEWORD(2, 2), &wsa);
    if (port_open()) {
        ShellExecuteA(NULL, "open", "http://127.0.0.1:8765", NULL, NULL, SW_SHOWNORMAL);
        return 0;
    }

    join(python, MAX_PATH, dir, "python\\python.exe");
    join(tess, MAX_PATH, dir, "tesseract\\tesseract.exe");
    snprintf(prefix, MAX_PATH, "%s\\tesseract\\tessdata\\", dir);
    join(apppath, MAX_PATH, dir, "app");
    SetEnvironmentVariableA("TESSERACT_CMD", tess);
    SetEnvironmentVariableA("TESSDATA_PREFIX", prefix);
    SetEnvironmentVariableA("PYTHONPATH", apppath);
    SetEnvironmentVariableA("NARELA_HOME", dir);
    SetEnvironmentVariableA("PYTHONDONTWRITEBYTECODE", "1");

    demo = cmd && strstr(cmd, "--demo");
    snprintf(cmdline, sizeof cmdline, "\"%s\" -m ownerapp%s", python, demo ? " --demo" : "");
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    if (!CreateProcessA(NULL, cmdline, NULL, NULL, FALSE, CREATE_NEW_CONSOLE, NULL, dir, &si, &pi)) {
        MessageBoxA(NULL, "Could not start python\\python.exe in this folder.", "Bill Rent Manager", MB_OK | MB_ICONERROR);
        return 1;
    }
    Sleep(1800);
    ShellExecuteA(NULL, "open", "http://127.0.0.1:8765", NULL, NULL, SW_SHOWNORMAL);
    WaitForSingleObject(pi.hProcess, INFINITE);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return 0;
}
