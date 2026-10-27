#!/usr/bin/env python3
"""Package this dependency-free local extension without npm or remote publishing."""
from pathlib import Path
import zipfile

here = Path(__file__).resolve().parent
target = here / 'gamepad-codex-focus-0.1.0.vsix'
manifest = '''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata><Identity Language="en-US" Id="gamepad-codex-focus" Version="0.1.0" Publisher="local-gamepad"/>
    <DisplayName>Gamepad Codex Focus Adapter</DisplayName>
    <Description xml:space="preserve">Local focus metadata for the Codex model switcher.</Description>
    <Tags>local</Tags><Categories>Other</Categories>
    <Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.121.0"/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="ui"/></Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
  <Dependencies/>
  <Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/></Assets>
</PackageManifest>'''
types = '''<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="js" ContentType="application/javascript"/>
  <Default Extension="vsixmanifest" ContentType="text/xml"/>
</Types>'''
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('extension.vsixmanifest', manifest)
    archive.writestr('[Content_Types].xml', types)
    for name in ('package.json', 'extension.js'):
        archive.write(here / name, 'extension/' + name)
print(target)
