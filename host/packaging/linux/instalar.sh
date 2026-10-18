#!/bin/sh
# Instala Planta para el usuario actual (sin sudo):
#   app en ~/.local/share/planta, acceso en el menú de aplicaciones y
#   el comando "planta-gui" en ~/.local/bin.
# Uso: ./instalar.sh            Desinstalar: ./instalar.sh --desinstalar
set -e
DEST="$HOME/.local/share/planta"
BIN="$HOME/.local/bin"
DESKTOP="$HOME/.local/share/applications/planta.desktop"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$1" = "--desinstalar" ]; then
    rm -rf "$DEST" "$DESKTOP" "$BIN/planta-gui"
    echo "Planta desinstalada."
    exit 0
fi

rm -rf "$DEST"
mkdir -p "$DEST" "$BIN" "$(dirname "$DESKTOP")"
cp -r "$HERE/Planta" "$HERE/_internal" "$DEST/"
cp "$HERE/icon.png" "$DEST/icon.png"
ln -sf "$DEST/Planta" "$BIN/planta-gui"
cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=Planta motor-generador
Comment=Adquisición de la planta motor-generador DC
Exec=$DEST/Planta
Icon=$DEST/icon.png
Terminal=false
Categories=Education;Science;Engineering;
StartupWMClass=planta
DESK
command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP")" || true

echo "Instalada en $DEST. Buscala como «Planta motor-generador» en el menú."
if ! id -nG | grep -qw dialout; then
    echo
    echo "ATENCIÓN: tu usuario no está en el grupo dialout y no va a poder abrir el puerto serie."
    echo "Corré:  sudo usermod -aG dialout \$USER   y cerrá la sesión."
fi
