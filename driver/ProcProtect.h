/* ============================================================
 * ProcProtect.h - Ring0 进程保护驱动头文件
 *
 * 功能: 定义 IOCTL 控制码、数据结构及函数声明
 * 架构: 仅支持 x64, Windows 7 SP1+
 * 框架: WDM (Windows Driver Model)
 * ============================================================ */

#ifndef _PROCPROTECT_H_
#define _PROCPROTECT_H_

#ifdef __cplusplus
extern "C" {
#endif

#include <ntddk.h>

/* ============================================================
 * 设备与符号链接名称
 * ============================================================ */
#define DEVICE_NAME         L"\\Device\\ProcProtect"
#define SYMBOLIC_LINK_NAME  L"\\DosDevices\\ProcProtect"

/* ============================================================
 * IOCTL 控制码定义
 * METHOD_BUFFERED: 使用系统缓冲区方式传输数据
 * ============================================================ */
#define IOCTL_PROCPROTECT_BASE     FILE_DEVICE_UNKNOWN

#define IOCTL_PROCPROTECT_ADD_PID \
    CTL_CODE(IOCTL_PROCPROTECT_BASE, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_PROCPROTECT_REMOVE_PID \
    CTL_CODE(IOCTL_PROCPROTECT_BASE, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_PROCPROTECT_CLEAR_ALL \
    CTL_CODE(IOCTL_PROCPROTECT_BASE, 0x802, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_PROCPROTECT_LIST_PIDS \
    CTL_CODE(IOCTL_PROCPROTECT_BASE, 0x803, METHOD_BUFFERED, FILE_ANY_ACCESS)

/* ============================================================
 * 受保护进程列表常量
 * ============================================================ */
#define MAX_PROTECTED_PIDS      64   /* 最大保护进程数 */
#define PROTECT_TAG             'tPrP' /* Pool Tag 用于内存分配追踪 */

/* ============================================================
 * 需要剥离的危险权限掩码
 * 当目标 PID 在受保护列表中时, 从 DesiredAccess 中移除这些权限
 * ============================================================ */
#define PROTECTED_ACCESS_MASK ( \
    PROCESS_TERMINATE       |   /* 终止进程 - TerminateProcess */     \
    PROCESS_VM_OPERATION    |   /* 操作虚拟内存 - VirtualProtectEx */  \
    PROCESS_VM_WRITE        |   /* 写入内存 - WriteProcessMemory */    \
    PROCESS_CREATE_THREAD   |   /* 创建远程线程 - CreateRemoteThread */\
    PROCESS_SUSPEND_RESUME  |   /* 挂起/恢复进程 */                    \
    PROCESS_SET_INFORMATION |   /* 设置进程信息 */                      \
    PROCESS_DUP_HANDLE)        /* 复制句柄 */

/* ============================================================
 * 数据结构定义
 * ============================================================ */

/*
 * PROTECT_PID_INFO - 单个 PID 信息结构
 * 用于 ADD_PID / REMOVE_PID 的输入输出缓冲区
 */
typedef struct _PROTECT_PID_INFO {
    ULONG Pid;                     /* 要操作的进程 ID */
} PROTECT_PID_INFO, *PPROTECT_PID_INFO;

/*
 * PID_LIST_HEADER - PID 列表头部信息
 * 用于 LIST_PIDS 的输出缓冲区
 */
typedef struct _PID_LIST_HEADER {
    ULONG Count;                   /* 当前受保护的 PID 数量 */
    ULONG Pids[MAX_PROTECTED_PIDS]; /* 受保护的 PID 数组 */
} PID_LIST_HEADER, *PPID_LIST_HEADER;

/* ============================================================
 * 全局状态变量声明
 * ============================================================ */

/* 受保护 PID 列表（在 ProcProtect.c 中定义） */
extern volatile LONG g_ProtectedPidList[MAX_PROTECTED_PIDS];
extern volatile LONG g_ProtectedPidCount;
extern KSPIN_LOCK g_PidListSpinLock;

/* 回调注册句柄 */
extern PVOID g_CallbackRegistration;

/* ============================================================
 * 函数声明
 * ============================================================ */

/* 驱动入口与卸载 */
DRIVER_INITIALIZE DriverEntry;
DRIVER_UNLOAD ProcProtect_Unload;

/* IRP 分发函数 */
NTSTATUS ProcProtect_CreateClose(
    IN PDEVICE_OBJECT DeviceObject,
    IN PIRP Irp);

NTSTATUS ProcProtect_DeviceControl(
    IN PDEVICE_OBJECT DeviceObject,
    IN PIRP Irp);

/* ObRegisterCallbacks 回调函数 */
OB_PRE_OPERATION_CALLBACK PreOpenProcessCallback(
    IN PVOID RegistrationContext,
    IN OB_PRE_OPERATION_INFORMATION OperationInformation);

OB_POST_OPERATION_CALLBACK PostOpenProcessCallback(
    IN PVOID RegistrationContext,
    IN OB_POST_OPERATION_INFORMATION OperationInformation);

/* 受保护 PID 列表操作（自旋锁保护） */
NTSTATUS AddProtectedPid(IN ULONG Pid);
NTSTATUS RemoveProtectedPid(IN ULONG Pid);
VOID ClearAllProtectedPids(VOID);
BOOLEAN IsPidProtected(IN ULONG Pid);

#ifdef __cplusplus
}
#endif

#endif /* _PROCPROTECT_H_ */
