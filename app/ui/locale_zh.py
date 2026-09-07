STATUS_MAP = {
    "READY": "就绪", "BUSY": "处理中", "DEPLOYING": "正在分发", "SCANNING": "正在扫描",
    "PREPARING": "准备中", "WAITING": "等待中", "RUNNING": "执行中", "SUCCESS": "成功",
    "FAILED": "失败", "PARTIAL_FAILED": "部分失败", "CANCELLED": "已取消", "SKIPPED": "已跳过",
    "COMPLETED": "已完成", "Completed": "已完成", "Cancelled": "已取消", "Failed": "失败",
    "Likely Windows": "疑似 Windows", "SMB": "SMB 可访问", "Online / Unknown": "在线 / 未确认",
}

ACTION_MAP = {
    "NEW": "新增", "UPDATE": "更新", "SAME": "相同", "STOP_SERVICE": "停止服务",
    "START_SERVICE": "启动服务", "KILL_PROCESS": "结束进程", "QUERY_SERVICE": "查询服务",
    "WAIT_SERVICE_STOPPED": "等待服务停止", "WAIT_SERVICE_RUNNING": "等待服务启动",
    "CMD": "CMD 命令", "PRE_CMD": "分发前 CMD", "POST_CMD": "分发后 CMD",
    "CREATE": "创建", "START": "开始", "FINISH": "完成", "TEST": "测试",
    "USER_START": "用户开始分发", "USER_CANCEL": "用户取消", "UI_COMPLETE": "界面完成通知",
    "HOST_START": "开始主机工作流", "HOST_FINISH": "完成主机工作流", "HOST_EXCEPTION": "主机工作流异常",
    "FILE_BEGIN": "开始处理文件", "FILE_ATTEMPT": "文件分发尝试", "FILE_ATTEMPT_FAILED": "文件尝试失败",
    "FILE_SUCCESS": "文件分发成功", "FILE_FAILED": "文件分发失败", "FILE_SKIP": "跳过相同文件",
    "CONNECT": "连接", "DISCONNECT": "断开连接", "TEMP_CLEANUP": "清理临时目录",
    "BATCH_START": "开始批量分发", "LOCAL_SELECTED": "选择本地分发源", "SFTP_PULL_START": "开始 SFTP 拉取",
    "SFTP_PULL_FINISH": "完成 SFTP 拉取", "MANIFEST_SAVED": "保存文件清单", "MANIFEST_DIGEST": "生成清单指纹",
    "ADD": "添加", "EDIT": "编辑", "DELETE": "删除", "ADD_HOSTS": "添加扫描主机",
    "SAVE": "保存", "HISTORY_CSV": "导出分发历史", "AUDIT_CSV": "导出审计日志",
    "OPEN_AUDIT_DIR": "打开审计目录", "CANCEL_REQUEST": "请求取消任务", "EXCEPTION": "异常",
    "SELECT_FILE": "选择文件", "SELECT_FOLDER": "选择目录", "HOST_FOUND": "发现主机",
    "VERIFY_HOSTNAME_START": "开始主机名验证", "VERIFY_HOSTNAME": "验证主机名", "VERIFY_HOSTNAME_FINISH": "完成主机名验证",
    "PRE_START": "开始分发前远程操作", "UI_TEST": "连接测试", "POST_RECONNECT": "分发后重新连接",
    "POST_SKIPPED": "跳过分发后操作", "TARGET_WRITE_PERMISSION": "目标目录写权限",
    "BACKUP_WRITE_PERMISSION": "备份目录写权限", "TARGET_FREE_SPACE": "目标磁盘空间",
    "BACKUP_FREE_SPACE": "备份磁盘空间", "TEMP_SIZE": "临时文件大小校验", "TEMP_SHA256": "临时文件 SHA256 校验",
    "BACKUP_SIZE": "备份文件大小校验", "BACKUP_SHA256": "备份文件 SHA256 校验",
    "FINAL_SIZE": "正式文件大小校验", "FINAL_SHA256": "正式文件 SHA256 校验",
    "EXISTING_SHA256": "现有文件 SHA256 对比", "EXISTING_COMPARE": "现有文件对比",
    "MAPPING_START": "开始分发映射", "MAPPING_FINISH": "完成分发映射", "MAPPING_PLAN": "生成分发计划", "PLAN_SAVED": "保存分发映射计划", "SOURCE_KIND_CORRECT": "修正源类型",
    "CONNECTIVITY_TEST_START": "开始在线测试", "CONNECTIVITY_TEST": "在线测试", "HOST_INVENTORY_TEST": "主机管理 SMB 测试",
    "CLEAR": "清空",
}

PHASE_MAP = {"PRE": "分发前", "POST": "分发后"}
SOURCE_MAP = {"LOCAL": "本地文件 / 目录", "SFTP": "SFTP 远程服务器", "MULTI": "多源多目标映射"}
MODE_MAP = {"WINRM": "WinRM", "ADMIN_SHARE": "Windows 管理共享（兼容）", "UNC": "UNC 共享路径（兼容）"}
GROUP_MAP = {"Default": "默认", "Discovered": "自动发现"}

AUDIT_CATEGORY_MAP = {
    "APP": "程序", "TASK": "任务", "SOURCE": "分发源", "DISCOVERY": "主机发现", "HOST": "主机管理",
    "SFTP": "SFTP", "SMB": "SMB", "WINRM": "WinRM", "PREFLIGHT": "分发前预检查",
    "DISTRIBUTION": "文件分发", "MAPPING": "分发映射", "BACKUP": "备份", "VERIFY": "校验", "SETTINGS": "系统设置", "EXPORT": "导出",
}


HOSTNAME_SOURCE_MAP = {
    "DNS_PTR": "DNS PTR",
    "NETBIOS": "NetBIOS",
    "WINDOWS_RESOLVER": "Windows 名称解析",
    "NETAPI_WKSTA": "Windows 工作站 API",
    "SMB_NTLM": "SMB/NTLM 指纹",
    "SMB_RPC": "SMB/WKSSVC 验证",
    "WINRM": "WinRM hostname",
    "MANUAL": "手工维护",
}

HOSTNAME_VERIFY_MAP = {
    "VERIFIED": "已验证",
    "UNVERIFIED": "未验证",
    "DUPLICATE": "名称重复，建议验证",
    "UNRESOLVED": "未识别",
    "FAILED": "验证失败",
}

VERIFY_STAGE_MAP = {
    "SOURCE_SIZE": "源文件大小", "SOURCE_SHA256": "源文件 SHA256", "EXISTING_SHA256": "现有目标文件 SHA256",
    "TARGET_WRITE_PERMISSION": "目标目录写权限", "BACKUP_WRITE_PERMISSION": "备份目录写权限",
    "TARGET_FREE_SPACE": "目标磁盘空间", "BACKUP_FREE_SPACE": "备份磁盘空间",
    "TEMP_SIZE": "临时文件大小", "TEMP_SHA256": "临时文件 SHA256",
    "BACKUP_SIZE": "备份文件大小", "BACKUP_SHA256": "备份文件 SHA256",
    "FINAL_SIZE": "正式文件大小", "FINAL_SHA256": "正式文件 SHA256",
}


def _map(mapping, value):
    if value is None:
        return ""
    s = str(value)
    return mapping.get(s, s)


def status_text(value): return _map(STATUS_MAP, value)
def action_text(value): return _map(ACTION_MAP, value)
def phase_text(value): return _map(PHASE_MAP, value)
def source_text(value): return _map(SOURCE_MAP, value)
def mode_text(value): return _map(MODE_MAP, value)
def group_text(value): return _map(GROUP_MAP, value)
def audit_category_text(value): return _map(AUDIT_CATEGORY_MAP, value)
def verify_stage_text(value): return _map(VERIFY_STAGE_MAP, value)


def hostname_source_text(value): return _map(HOSTNAME_SOURCE_MAP, value)
def hostname_verify_text(value): return _map(HOSTNAME_VERIFY_MAP, value)
