; HOS-ARES 安装包（B199）
;
; 交付对象是"拿到就能用"的人：装完点桌面/开始菜单的 HOS-ARES，直接出现控制台界面。
; 因此这里做三件事，而不是只把一个 exe 拷过去：
;   1) 装到 Program Files 下的独立目录，附卸载项（可干净移除）；
;   2) 建**开始菜单**与**桌面**快捷方式（"快捷启动"就是这一步）；
;   3) 装完的收尾页可以直接勾"立即打开"，省掉"装完了该点哪儿"的一步。
;
; 用法（CI 与本地一致）：
;   ISCC.exe /DMyAppVersion=0.1.0 packaging\HOS-ARES.iss
; 产物：packaging\out\HOS-ARES-Setup-<版本>.exe

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "HOS-ARES"
#define MyAppPublisher "HOS"
#define MyAppExeName "HOS-ARES.exe"
;: 仓库根（本文件在 packaging/ 下，编译时以本文件所在目录为基准）
#define RepoRoot ".."

[Setup]
AppId={{8E4C1F62-9A3D-4B71-8F2C-5D6A7B90E314}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 单用户装到 Program Files 需要提权：给管理员权限，普通用户点"是"即可
PrivilegesRequired=admin
OutputDir={#RepoRoot}\packaging\out
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
; 安装包本身也用现代图标位置（没有图标文件时不指定，避免编译失败）

[Languages]
Name: "chs"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Files]
; PyInstaller 的单文件产物（workflow 里先跑 PyInstaller，再跑本脚本）
Source: "{#RepoRoot}\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 说明书随装随带：出问题时先看它，而不是先问人
Source: "{#RepoRoot}\README.md"; DestDir: "{app}"; DestName: "README.md"; Flags: ignoreversion
Source: "{#RepoRoot}\docs\exe-quickstart.txt"; DestDir: "{app}"; DestName: "快速开始.txt"; \
    Flags: ignoreversion skipifsourcedoesntexist

[Icons]
; 开始菜单：主入口
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "打开 HOS-ARES 控制台界面"
Name: "{group}\快速开始"; Filename: "{app}\快速开始.txt"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
; 桌面：勾了才建
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; \
    Comment: "打开 HOS-ARES 控制台界面"

[Run]
; "安装完成后打开后直接是界面"——收尾页勾一下就起
Filename: "{app}\{#MyAppExeName}"; Description: "立即打开 {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删安装目录里我们放的东西；用户自己的产物（out/）不在 {app} 下，一律不动
Type: filesandordirs; Name: "{app}"
