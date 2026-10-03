; Tweeq installer. Built by release/build_release.sh:
;   ISCC /DAppVersion=0.1.0 /DAppVersion4=0.1.0.0 /DStage=<staged files> /DOutDir=<output folder> tweeq.iss
; One click: a single "Ready to install" page, then Install. Per-user (no administrator prompt), desktop + Start Menu icons.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppVersion4
  #define AppVersion4 "0.0.0.0"
#endif
#ifndef Stage
  #error Stage (the folder with the files to install) must be defined with /DStage=...
#endif
#ifndef OutDir
  #define OutDir "."
#endif

[Setup]
AppId={{6C1B7B52-3E0F-4E57-9A53-7D2E61F4A8B1}
AppName=Tweeq
AppVersion={#AppVersion}
AppVerName=Tweeq {#AppVersion}
AppPublisher=Brian Thomason
AppPublisherURL=https://github.com/iamfuzz/tweeq
AppSupportURL=https://github.com/iamfuzz/tweeq/issues
AppUpdatesURL=https://github.com/iamfuzz/tweeq/releases
AppCopyright=Copyright (C) 2026 Brian Thomason - GPL-3.0
DefaultDirName={autopf}\Tweeq
DefaultGroupName=Tweeq
PrivilegesRequired=lowest
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
ShowLanguageDialog=no
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=Tweeq-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Tweeq.exe
UninstallDisplayName=Tweeq
VersionInfoVersion={#AppVersion4}
VersionInfoCompany=Brian Thomason
VersionInfoDescription=Tweeq setup
VersionInfoProductName=Tweeq
#ifdef FastCompression
Compression=zip/1
#else
Compression=lzma2/ultra64
SolidCompression=yes
#endif
WizardStyle=modern
CloseApplications=yes
SetupLogging=yes

[Messages]
ReadyLabel1=Ready to install Tweeq
ReadyLabel2a=Tweeq will be installed for your Windows account. No administrator rights are needed. Click Install; a Tweeq icon will be added to your desktop.

[InstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\Tweeq"; Filename: "{app}\Tweeq.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\Tweeq"; Filename: "{app}\Tweeq.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\Tweeq.exe"; Description: "Start Tweeq now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\python"

[Code]
// Command-line switch value, e.g. /TWEEQDATA=C:\x . Works in setup AND in the uninstaller (the {param:} constant is not
// documented for uninstall time), and lets the automated tests point at a throw-away data folder.
function CmdValue(const Name, Default: String): String;
var
  i: Integer;
  S, Prefix: String;
begin
  Result := Default;
  Prefix := '/' + Uppercase(Name) + '=';
  for i := 1 to ParamCount do
  begin
    S := ParamStr(i);
    if Pos(Prefix, Uppercase(S)) = 1 then
    begin
      Result := Copy(S, Length(Prefix) + 1, MaxInt);
      exit;
    end;
  end;
end;

function DataRoot: String;
begin
  Result := RemoveBackslashUnlessRoot(CmdValue('TWEEQDATA', ExpandConstant('{userappdata}\Tweeq')));
end;

function HasVault: Boolean;
var
  FindRec: TFindRec;
begin
  Result := FileExists(DataRoot + '\vault\manifest.json');
  if (not Result) and FindFirst(DataRoot + '\vault_*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          if FileExists(DataRoot + '\' + FindRec.Name + '\manifest.json') then
          begin
            Result := True;
            break;
          end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'Tweeq {#AppVersion} will be installed to:' + NewLine + Space + ExpandConstant('{app}') + NewLine + NewLine +
    'Tweeq changes only your own EverQuest files, and keeps the originals so they can be restored.' + NewLine + NewLine +
    'Tweeq is free software under the GNU General Public License v3. Source: github.com/iamfuzz/tweeq' + NewLine + NewLine +
    'Not affiliated with or endorsed by Daybreak Game Company. EverQuest is a trademark of Daybreak Game Company LLC.';
end;

// Uninstall: first offer to put the user's EverQuest files back, then (only if that worked) offer to delete Tweeq's data.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  rc: Integer;
  Restored, KeepData, Again: Boolean;
begin
  if CurUninstallStep <> usUninstall then exit;
  KeepData := True;
  if HasVault and (CmdValue('KEEPMODS', '0') <> '1') then
  begin
    Restored := False;
    if SuppressibleMsgBox('Put your EverQuest files back to their originals before removing Tweeq?' + #13#10 + #13#10 +
         'This is recommended. Your swaps are switched off, not deleted; reinstalling Tweeq lets you switch them back on.',
         mbConfirmation, MB_YESNO, IDYES) = IDYES then
    begin
      repeat
        Again := False;
        if Exec(ExpandConstant('{app}\python\python.exe'),
             '-X utf8 -B -m tweeq.cli --json uninstall-restore --data-root "' + DataRoot + '"',
             ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, rc) then
        begin
          if rc = 0 then
            Restored := True
          else if rc = 4 then
            Again := SuppressibleMsgBox('EverQuest is running. Close it completely, then click Retry.',
              mbError, MB_RETRYCANCEL, IDCANCEL) = IDRETRY
          else
            SuppressibleMsgBox('Some of your EverQuest files could not be restored (a game patch may have replaced them, or the EverQuest folder could not be found). ' +
              'Tweeq left those files alone, and its saved originals are kept in ' + DataRoot + '.',
              mbInformation, MB_OK, IDOK);
        end
        else
          SuppressibleMsgBox('Tweeq could not run its restore step, so your EverQuest files were left as they are. ' +
            'Its saved originals are kept in ' + DataRoot + '.', mbInformation, MB_OK, IDOK);
      until not Again;
      if Restored and (SuppressibleMsgBox('Also delete Tweeq''s settings, previews and saved original files?' + #13#10 + #13#10 +
           'If you might reinstall Tweeq, choose No.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then
        KeepData := False;
    end;
  end
  else if (not HasVault) and (CmdValue('KEEPMODS', '0') <> '1') then
    KeepData := False;      // never swapped anything: only settings and cached previews, nothing worth keeping
  if not KeepData then
    DelTree(DataRoot, True, True, True);
end;
