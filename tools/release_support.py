"""构建管理辅助：60个日历日、Windows DPAPI 私钥存储，不进入产品包。"""
import ctypes
from ctypes import wintypes
from datetime import date, timedelta
import os


def license_dates(start=None, days=60):
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3660:
        raise ValueError("授权天数必须为 1..3660 的整数")
    first = date.fromisoformat(str(start)) if start else date.today()
    # 现有验证器在 expires 当天仍有效，因此减1，避免实际成为61天。
    return first, first + timedelta(days=days - 1)


def protect_key(data, decrypt=False):
    """DPAPI 仅当前 Windows 用户可解密；管理员需保护/备份该账户与密钥文件。"""
    if os.name != "nt":
        raise RuntimeError("发布私钥存储需要 Windows DPAPI")
    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.length)
    finally:
        kernel.LocalFree(output.data)
