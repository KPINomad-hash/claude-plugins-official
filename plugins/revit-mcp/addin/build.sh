#!/usr/bin/env bash
# Builds the add-in for Revit 2025, 2026 and 2027 and assembles the release
# folder and zip in addin/dist. Needs the .NET SDK 8 and 10 and `zip`. Works on
# Linux, macOS and Windows (Git Bash): the Revit API is referenced through NuGet
# reference packages, so Revit itself is not needed to build.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

version="$(sed -n 's:.*<Version>\(.*\)</Version>.*:\1:p' Directory.Build.props)"
name="RevitMcp-$version"
out="dist/$name"
rm -rf dist
mkdir -p "$out/addin" "$out/server"

for year in 2025 2026 2027; do
  dotnet build src/RevitMcp.Addin -c Release -p:RevitYear="$year" -v q -nologo
  mkdir -p "$out/addin/$year"
  cp "bin/Release/$year/RevitMcp.Addin.dll" "$out/addin/$year/"
done

cp RevitMcp.addin "$out/addin/"
cp install.ps1 "$out/"
cp ../README.md ../LICENSE "$out/"
cp -r ../server/index.mjs ../server/package.json ../server/lib "$out/server/"

# Claude Desktop extension: manifest.json + server/ at the archive root.
mcpb="$PWD/$out/revit-mcp-$version.mcpb"
(cd "$out" && zip -q -r "$mcpb" server)
(cd ../desktop && zip -q "$mcpb" manifest.json)

(cd dist && zip -q -r "$name.zip" "$name" && sha256sum "$name.zip" > "$name.zip.sha256")
echo "built addin/dist/$name.zip"
