/* ============================================================
 * ProcProtect.c - Ring0 进程保护驱动主源码
 *
 * 核心机制:
 *   1. 通过 ObRegisterCallbacks 注册对象回调
 *   2. 在 PreOperation 阶段拦截 OpenProcess 调用
 *   3. 检查目标 PID 是否在受保护列表中
 *   4. 若受保护, 从 DesiredAccess 中剥离危险权限
 *   5. 结果: 调用方获得无终止权限的句柄, TerminateProcess 失败
 *
 * IOCTL 接口:
 *   - ADD_PID:     将 PID 加入受保护列表
 *   - REMOVE_PID:  从列表移除指定 PID
 *   - CLEAR_ALL:   清空整个保护列表
 *   - LIST_PIDS:   返回当前所有受保护 PID
 *
 * 线程安全: 所有列表操作使用 KSPIN_LOCK 同步
 * 兼容性: Windows 7 SP1+, 仅 x64
 * ============================================================ */

#include "ProcProtect.h"

/* ============================================================
 * 全局变量定义
 * ============================================================ */

/* 受保护 PID 列表 */
volatile LONG g_ProtectedPidList[MAX_PROTECTED_PIDS] = { 0 };
volatile LONG g_ProtectedPidCount = 0;

/* 自旋锁: 保护 PID 列表的并发访问 */
KSPIN_LOCK g_PidListSpinLock;

/* ObRegisterCallbacks 注册句柄 (卸载时必须反注册) */
PVOID g_CallbackRegistration = NULL;

/* 设备对象指针 (卸载时删除) */
PDEVICE_OBJECT g_DeviceObject = NULL;

/* 符号链接创建标志 */
BOOLEAN g_SymbolicLinkCreated = FALSE;

/* ============================================================
 * 内部辅助函数: PID 列表操作
 * 所有函数调用前必须已持有自旋锁
 * ============================================================ */

/*
 * IsPidInListLocked - 在持有锁的情况下检查 PID 是否已在列表中
 * 注意: 此函数要求调用者已持有 g_PidListSpinLock!
 */
static BOOLEAN IsPidInListLocked(ULONG Pid)
{
    LONG i, count;

    count = g_ProtectedPidCount;
    for (i = 0; i < count; i++) {
        if ((ULONG)g_ProtectedPidList[i] == Pid) {
            return TRUE;
        }
    }
    return FALSE;
}

/* ============================================================
 * 公开函数: PID 列表操作 (带自旋锁保护)
 * ============================================================ */

/*
 * AddProtectedPid - 将 PID 加入受保护列表
 * 成功返回 STATUS_SUCCESS, 列表满返回 STATUS_INSUFFICIENT_RESOURCES
 */
NTSTATUS AddProtectedPid(IN ULONG Pid)
{
    KIRQL oldIrql;
    LONG count;

    if (Pid == 0) {
        return STATUS_INVALID_PARAMETER;
    }

    KeAcquireSpinLock(&g_PidListSpinLock, &oldIrql);

    /* 先检查是否已存在, 避免重复添加 */
    if (IsPidInListLocked(Pid)) {
        KeReleaseSpinLock(&g_PidListSpinLock, oldIrql);
        return STATUS_SUCCESS;
    }

    count = g_ProtectedPidCount;
    if (count >= MAX_PROTECTED_PIDS) {
        KeReleaseSpinLock(&g_PidListSpinLock, oldIrql);
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    g_ProtectedPidList[count] = (LONG)Pid;
    InterlockedIncrement(&g_ProtectedPidCount);

    KeReleaseSpinLock(&g_PidListSpinLock, oldIrql);

    DbgPrint("[ProcProtect] PID %lu added to protected list (total: %ld)\n",
             Pid, g_ProtectedPidCount);

    return STATUS_SUCCESS;
}

/*
 * RemoveProtectedPid - 从受保护列表中移除指定 PID
 * 找到并移除返回 STATUS_SUCCESS, 未找到返回 STATUS_NOT_FOUND
 */
NTSTATUS RemoveProtectedPid(IN ULONG Pid)
{
    KIRQL oldIrql;
    LONG i, count;
    BOOLEAN found = FALSE;

    if (Pid == 0) {
        return STATUS_INVALID_PARAMETER;
    }

    KeAcquireSpinLock(&g_PidListSpinLock, &oldIrql);

    count = g_ProtectedPidCount;
    for (i = 0; i < count; i++) {
        if ((ULONG)g_ProtectedPidList[i] == Pid) {
            found = TRUE;
            /* 将后续元素前移填补空位 */
            for (; i < count - 1; i++) {
                g_ProtectedPidList[i] = g_ProtectedPidList[i + 1];
            }
            g_ProtectedPidList[count - 1] = 0;
            InterlockedDecrement(&g_ProtectedPidCount);
            break;
        }
    }

    KeReleaseSpinLock(&g_PidListSpinLock, oldIrql);

    if (found) {
        DbgPrint("[ProcProtect] PID %lu removed from protected list (total: %ld)\n",
                 Pid, g_ProtectedPidCount);
        return STATUS_SUCCESS;
    }

    return STATUS_NOT_FOUND;
}

/*
 * ClearAllProtectedPids - 清空整个受保护 PID 列表
 */
VOID ClearAllProtectedPids(VOID)
{
    KIRQL oldIrql;
    LONG i, count;

    KeAcquireSpinLock(&g_PidListSpinLock, &oldIrql);

    count = g_ProtectedPidCount;
    for (i = 0; i < count; i++) {
        g_ProtectedPidList[i] = 0;
    }
    g_ProtectedPidCount = 0;

    KeReleaseSpinLock(&g_PidListSpinLock, oldIrql);

    DbgPrint("[ProcProtect] All protected PIDs cleared\n");
}

/*
 * IsPidProtected - 检查指定 PID 是否当前受保护
 * 用于 PreOperation 回调中的快速查找
 */
BOOLEAN IsPidProtected(IN ULONG Pid)
{
    KIRQL oldIrql;
    BOOLEAN result;

    KeAcquireSpinLock(&g_PidListSpinLock, &oldIrql);
    result = IsPidInListLocked(Pid);
    KeReleaseSpinLock(&g_PidListSpinLock, oldIrql);

    return result;
}

/* ============================================================
 * ObRegisterCallbacks 回调实现
 * ============================================================ */

/*
 * PreOpenProcessCallback - OpenProcess 前置回调 (核心权限剥离逻辑)
 *
 * 触发时机: 任何进程调用 NtOpenProcess / OpenProcess 时
 * 操作: 检查目标 PID 是否受保护, 若是则清除危险访问权限
 *
 * 性能: 仅做数组遍历 + 位运算, O(64) 最坏情况, 微秒级延迟
 */
OB_PRE_OPERATION_CALLBACK PreOpenProcessCallback(
    IN PVOID RegistrationContext,
    IN OB_PRE_OPERATION_INFORMATION OperationInformation)
{
    UNREFERENCED_PARAMETER(RegistrationContext);

    /* 仅处理进程对象类型 */
    if (OperationInformation->ObjectType != *PsProcessType) {
        return;
    }

    /* 仅在操作可修改时才处理 */
    if (OperationInformation->Operation != OB_PRE_OPERATION_CREATE &&
        OperationInformation->Operation != OB_PRE_OPERATION_DUPLICATE_HANDLE) {
        return;
    }

    /* 获取目标进程 ID */
    HANDLE targetPid = PsGetProcessId(
        (PEPROCESS)OperationInformation->Object);

    /* 检查是否为受保护 PID */
    if (IsPidProtected((ULONG)(ULONG_PTR)targetPid)) {
        /* 剥离所有危险权限 - 这是核心操作! */
        OperationInformation->Parameters->CreateHandleInformation.DesiredAccess
            &= ~PROTECTED_ACCESS_MASK;

        DbgPrint("[ProcProtect] Access stripped for PID %lu by caller from PID %lu\n",
                 (ULONG)(ULONG_PTR)targetPid,
                 (ULONG)(ULONG_PTR)PsGetCurrentProcessId());
    }
}

/*
 * PostOpenProcessCallback - OpenProcess 后置回调
 * 当前为空操作, 保留用于未来扩展 (如审计日志记录)
 */
OB_POST_OPERATION_CALLBACK PostOpenProcessCallback(
    IN PVOID RegistrationContext,
    IN OB_POST_OPERATION_INFORMATION OperationInformation)
{
    UNREFERENCED_PARAMETER(RegistrationContext);
    UNREFERENCED_PARAMETER(OperationInformation);
    /* 空操作 - 可在此处添加日志/审计功能 */
    return;
}

/* ============================================================
 * IRP 分发处理函数
 * ============================================================ */

/*
 * ProcProtect_CreateClose - 处理 Create / Close IRP
 * 用户态程序打开/关闭 \\.\ProcProtect 设备时触发
 */
NTSTATUS ProcProtect_CreateClose(
    IN PDEVICE_OBJECT DeviceObject,
    IN PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);

    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);

    return STATUS_SUCCESS;
}

/*
 * ProcProtect_DeviceControl - IOCTL 命令分发处理
 * 解析用户态发送的 IOCTL 控制码并执行对应操作
 */
NTSTATUS ProcProtect_DeviceControl(
    IN PDEVICE_OBJECT DeviceObject,
    IN PIRP Irp)
{
    NTSTATUS              status = STATUS_SUCCESS;
    PIO_STACK_LOCATION    irpStack;
    ULONG                 ioControlCode;
    PVOID                 inputBuffer;
    PVOID                 outputBuffer;
    ULONG                 inputLength;
    ULONG                 outputLength;
    PPROTECT_PID_INFO     pidInfo;
    PPID_LIST_HEADER      listHeader;
    LONG                  i, count;
    KIRQL                 oldIrql;

    UNREFERENCED_PARAMETER(DeviceObject);

    irpStack = IoGetCurrentIrpStackLocation(Irp);
    ioControlCode = irpStack->Parameters.DeviceIoControl.IoControlCode;
    inputBuffer   = Irp->AssociatedIrp.SystemBuffer;
    outputBuffer  = Irp->AssociatedIrp.SystemBuffer;
    inputLength   = irpStack->Parameters.DeviceIoControl.InputBufferLength;
    outputLength  = irpStack->Parameters.DeviceIoControl.OutputBufferLength;

    switch (ioControlCode) {

    /* ---- IOCTL: 添加受保护 PID ---- */
    case IOCTL_PROCPROTECT_ADD_PID:
        if (inputLength < sizeof(PROTECT_PID_INFO)) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        pidInfo = (PPROTECT_PID_INFO)inputBuffer;
        status = AddProtectedPid(pidInfo->Pid);
        Irp->IoStatus.Information = 0;
        break;

    /* ---- IOCTL: 移除受保护 PID ---- */
    case IOCTL_PROCPROTECT_REMOVE_PID:
        if (inputLength < sizeof(PROTECT_PID_INFO)) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        pidInfo = (PPROTECT_PID_INFO)inputBuffer;
        status = RemoveProtectedPid(pidInfo->Pid);
        Irp->IoStatus.Information = 0;
        break;

    /* ---- IOCTL: 清空所有受保护 PID ---- */
    case IOCTL_PROCPROTECT_CLEAR_ALL:
        ClearAllProtectedPids();
        status = STATUS_SUCCESS;
        Irp->IoStatus.Information = 0;
        break;

    /* ---- IOCTL: 获取当前受保护 PID 列表 ---- */
    case IOCTL_PROCPROTECT_LIST_PIDS:
        if (outputLength < sizeof(PID_LIST_HEADER)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }
        listHeader = (PPID_LIST_HEADER)outputBuffer;

        /* 在锁保护下复制列表快照 */
        KeAcquireSpinLock(&g_PidListSpinLock, &oldIrql);
        count = g_ProtectedPidCount;
        listHeader->Count = (ULONG)count;
        for (i = 0; i < count && i < MAX_PROTECTED_PIDS; i++) {
            listHeader->Pids[i] = (ULONG)g_ProtectedPidList[i];
        }
        KeReleaseSpinLock(&g_PidListSpinLock, oldIrql);

        Irp->IoStatus.Information = sizeof(PID_LIST_HEADER);
        status = STATUS_SUCCESS;
        break;

    /* ---- 未知 IOCTL 码 ---- */
    default:
        status = STATUS_INVALID_DEVICE_REQUEST;
        Irp->IoStatus.Information = 0;
        break;
    }

    Irp->IoStatus.Status = status;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);

    return status;
}

/* ============================================================
 * 驱动入口与卸载
 * ============================================================ */

/*
 * DriverEntry - 驱动入口点
 * 执行步骤:
 *   1. 创建设备对象
 *   2. 创建符号链接 (用户态可通过 \\.\ProcProtect 访问)
 *   3. 初始化自旋锁
 *   4. 设置 IRP 分发函数
 *   5. 注册 ObRegisterCallbacks 回调
 */
NTSTATUS DriverEntry(
    IN PDRIVER_OBJECT DriverObject,
    IN PUNICODE_STRING RegistryPath)
{
    NTSTATUS           status;
    UNICODE_STRING     deviceName;
    UNICODE_STRING     linkName;
    OB_CALLBACK_REGISTRATION callbackReg;
    OPERATION_REGISTRATION  opReg;
    BOOLEAN            callbacksRegistered = FALSE;

    UNREFERENCED_PARAMETER(RegistryPath);

    DbgPrint("[ProcProtect] DriverEntry - Process protection driver loading...\n");

    /* 步骤 1: 创建设备对象 */
    RtlInitUnicodeString(&deviceName, DEVICE_NAME);
    status = IoCreateDevice(
        DriverObject,
        0,                       /* 设备扩展大小 */
        &deviceName,
        FILE_DEVICE_UNKNOWN,
        0,                       /* 无特殊特征 */
        FALSE,                   /* 非独占设备 */
        &g_DeviceObject);

    if (!NT_SUCCESS(status)) {
        DbgPrint("[ProcProtect] Failed to create device object: 0x%X\n", status);
        goto cleanup;
    }

    /* 步骤 2: 创建符号链接 (DOS 设备名) */
    RtlInitUnicodeString(&linkName, SYMBOLIC_LINK_NAME);
    status = IoCreateSymbolicLink(&linkName, &deviceName);
    if (!NT_SUCCESS(status)) {
        DbgPrint("[ProcProtect] Failed to create symbolic link: 0x%X\n", status);
        goto cleanup_device;
    }
    g_SymbolicLinkCreated = TRUE;

    /* 步骤 3: 初始化自旋锁 */
    KeInitializeSpinLock(&g_PidListSpinLock);

    /* 步骤 4: 设置 IRP 分发函数 */
    DriverObject->MajorFunction[IRP_MJ_CREATE]          = ProcProtect_CreateClose;
    DriverObject->MajorFunction[IRP_MJ_CLOSE]           = ProcProtect_CreateClose;
    DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL]  = ProcProtect_DeviceControl;
    DriverObject->DriverUnload                          = ProcProtect_Unload;

    /* 步骤 5: 注册 ObRegisterCallbacks 回调 */
    /*
     * 关键: ObRegisterCallbacks 要求:
     *   - Altitude 字符串标识回调优先级
     *   - 必须注册 Post 回调 (即使为空操作)
     *   - Windows 8+ 要求驱动必须经过 EV 签名
     */
    RtlZeroMemory(&callbackReg, sizeof(callbackReg));
    RtlZeroMemory(&opReg, sizeof(opReg));

    callbackReg.Version                    = OB_FLT_REGISTRATION_VERSION;
    callbackReg.OperationRegistrationCount  = 1;
    callbackReg.RegistrationContext         = NULL;
    callbackReg.OperationRegistration       = &opReg;

    opReg.ObjectType                        = PsProcessType;
    opReg.Operations                        = OB_PRE_OPERATION_CREATE | OB_PRE_OPERATION_DUPLICATE_HANDLE;
    opReg.PreOperation                      = PreOpenProcessCallback;
    opReg.PostOperation                     = PostOpenProcessCallback;

    RtlInitUnicodeString(&callbackReg.Altitude, L"360000");  /* 高优先级 */

    status = ObRegisterCallbacks(&callbackReg, &g_CallbackRegistration);
    if (!NT_SUCCESS(status)) {
        DbgPrint("[ProcProtect] Failed to register callbacks: 0x%X\n", status);
        goto cleanup_link;
    }
    callbacksRegistered = TRUE;

    DbgPrint("[ProcProtect] Driver loaded successfully! Device: %wS\n",
             DEVICE_NAME);

    return STATUS_SUCCESS;

    /* ===== 错误清理链 ===== */
cleanup_link:
    if (g_SymbolicLinkCreated) {
        UNICODE_STRING ln;
        RtlInitUnicodeString(&ln, SYMBOLIC_LINK_NAME);
        IoDeleteSymbolicLink(&ln);
        g_SymbolicLinkCreated = FALSE;
    }

cleanup_device:
    if (g_DeviceObject) {
        IoDeleteDevice(g_DeviceObject);
        g_DeviceObject = NULL;
    }

cleanup:
    DbgPrint("[ProcProtect] DriverEntry failed: 0x%X\n", status);
    return status;
}

/*
 * ProcProtect_Unload - 驱动卸载例程
 *
 * !!! 重要 !!!
 * 必须按以下顺序执行清理, 否则将导致 BSOD:
 *   1. 反注册 ObRegisterCallbacks (最先!)
 *   2. 删除符号链接
 *   3. 删除设备对象
 *
 * 如果跳过步骤 1 直接卸载驱动, 系统将在下次触发回调时蓝屏
 * 因为回调代码所在的驱动内存已被释放
 */
VOID ProcProtect_Unload(IN PDRIVER_OBJECT DriverObject)
{
    UNICODE_STRING linkName;

    DbgPrint("[ProcProtect] Driver unloading...\n");

    /* 步骤 1: 反注册回调 - 最关键! */
    if (g_CallbackRegistration != NULL) {
        ObUnRegisterCallbacks(g_CallbackRegistration);
        g_CallbackRegistration = NULL;
        DbgPrint("[ProcProtect] Callbacks unregistered\n");
    }

    /* 步骤 2: 清空保护列表 */
    ClearAllProtectedPids();

    /* 步骤 3: 删除符号链接 */
    if (g_SymbolicLinkCreated) {
        RtlInitUnicodeString(&linkName, SYMBOLIC_LINK_NAME);
        IoDeleteSymbolicLink(&linkName);
        g_SymbolicLinkCreated = FALSE;
    }

    /* 步骤 4: 删除设备对象 */
    if (g_DeviceObject != NULL) {
        IoDeleteDevice(g_DeviceObject);
        g_DeviceObject = NULL;
    }

    DbgPrint("[ProcProtect] Driver unloaded successfully\n");
}
