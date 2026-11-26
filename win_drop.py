"""Accept Explorer file drops on a Tk window using the Windows shell."""

from ctypes import WINFUNCTYPE, WinDLL, c_size_t, c_ssize_t, create_unicode_buffer
from ctypes.wintypes import BOOL, HANDLE, HWND, LPARAM, LPWSTR, UINT, WPARAM


WM_DROPFILES = 0x0233
WM_NCDESTROY = 0x0082
SUBCLASSPROC = WINFUNCTYPE(c_ssize_t, HWND, UINT, WPARAM, LPARAM, c_size_t, c_size_t)


def accept_file_drops(window, task_list, receive):
    """Accept drops on the window and task list. Keep the returned callback alive."""
    controls = WinDLL("comctl32")
    shell = WinDLL("shell32")
    controls.SetWindowSubclass.argtypes = (HWND, SUBCLASSPROC, c_size_t, c_size_t)
    controls.SetWindowSubclass.restype = BOOL
    controls.RemoveWindowSubclass.argtypes = (HWND, SUBCLASSPROC, c_size_t)
    controls.RemoveWindowSubclass.restype = BOOL
    controls.DefSubclassProc.argtypes = (HWND, UINT, WPARAM, LPARAM)
    controls.DefSubclassProc.restype = c_ssize_t
    shell.DragAcceptFiles.argtypes = (HWND, BOOL)
    shell.DragAcceptFiles.restype = None
    shell.DragQueryFileW.argtypes = (HANDLE, UINT, LPWSTR, UINT)
    shell.DragQueryFileW.restype = UINT
    shell.DragFinish.argtypes = (HANDLE,)
    shell.DragFinish.restype = None

    @SUBCLASSPROC
    def dispatch(hwnd, message, wparam, lparam, subclass_id, _data):
        if message == WM_DROPFILES:
            paths = []
            try:
                count = shell.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                for index in range(count):
                    length = shell.DragQueryFileW(wparam, index, None, 0)
                    buffer = create_unicode_buffer(length + 1)
                    if shell.DragQueryFileW(wparam, index, buffer, len(buffer)):
                        paths.append(buffer.value)
            finally:
                shell.DragFinish(wparam)
            if paths:
                window.after_idle(receive, paths)
            return 0
        if message == WM_NCDESTROY:
            controls.RemoveWindowSubclass(hwnd, dispatch, subclass_id)
        return controls.DefSubclassProc(hwnd, message, wparam, lparam)

    installed = False
    for hwnd in (int(window.wm_frame(), 0), task_list.winfo_id()):
        if controls.SetWindowSubclass(hwnd, dispatch, 0, 0):
            shell.DragAcceptFiles(hwnd, True)
            installed = True
    if not installed:
        raise OSError("Could not register the Windows file-drop callback")
    return dispatch
