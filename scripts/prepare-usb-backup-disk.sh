#!/usr/bin/env bash
# Phase 3, Stufe 1: USB-SSD als Sicherungsziel vorbereiten (EINMALIG, mit sudo ausführen).
#
#   sudo ./scripts/prepare-usb-backup-disk.sh
#
# LÖSCHT DIE GESAMTE PLATTE. Schutzmaßnahmen:
#  - nur die Platte mit genau dieser Seriennummer wird angefasst (nicht /dev/sdX blind)
#  - Abbruch, wenn eine Partition der Platte eingehängt ist
#  - Abbruch, wenn die Platte die System-SSD (nvme) wäre
#  - ausdrückliche Bestätigung durch Eintippen von LOESCHEN
# Ergebnis: eine ext4-Partition "EMS-BACKUP", fest eingehängt unter /mnt/ems-backup
# (fstab per UUID, nofail -> Server startet auch ohne angesteckte Platte), Besitzer horst,
# Markierungsdatei .ems-backup-ziel (der Backup-Dienst schreibt NUR, wenn sie existiert –
# sonst landete die Sicherung unbemerkt auf der System-SSD).
set -euo pipefail

SERIAL="4935234489905930"          # Verbatim Vi550 S3, 512 GB (erkannt 19.10.2026)
MOUNT=/mnt/ems-backup
LABEL=EMS-BACKUP
OWNER=horst

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen."; exit 1; }

DEV=""
for d in /sys/block/sd*; do
  name=$(basename "$d")
  s=$(udevadm info --query=property --name="/dev/$name" | sed -n 's/^ID_SERIAL_SHORT=//p')
  [ "$s" = "$SERIAL" ] && DEV="/dev/$name"
done
[ -n "$DEV" ] || { echo "Platte mit Seriennummer $SERIAL nicht gefunden – ist sie angesteckt?"; exit 1; }
case "$DEV" in /dev/nvme*) echo "Sicherheitsabbruch: $DEV ist eine NVMe-Platte"; exit 1;; esac

if lsblk -nro MOUNTPOINTS "$DEV" | grep -q .; then
  echo "Abbruch: Eine Partition von $DEV ist eingehängt:"; lsblk "$DEV"; exit 1
fi

echo "Gefunden: $DEV  ($(udevadm info --query=property --name="$DEV" | sed -n 's/^ID_MODEL=//p'), $(lsblk -dno SIZE "$DEV"))"
lsblk -o NAME,SIZE,FSTYPE,LABEL "$DEV"
echo
echo "ALLE Daten auf $DEV werden unwiderruflich gelöscht."
read -r -p "Zum Fortfahren LOESCHEN eintippen: " answer
[ "$answer" = "LOESCHEN" ] || { echo "Abgebrochen – nichts verändert."; exit 1; }

wipefs -a "$DEV"
parted -s "$DEV" mklabel gpt mkpart "$LABEL" ext4 1MiB 100%
partprobe "$DEV"; sleep 2
PART="${DEV}1"
mkfs.ext4 -F -L "$LABEL" -m 0 "$PART"
UUID=$(blkid -s UUID -o value "$PART")

mkdir -p "$MOUNT"
if ! grep -q "$UUID" /etc/fstab; then
  cp /etc/fstab "/etc/fstab.bak-$(date +%Y%m%d-%H%M%S)"
  echo "UUID=$UUID $MOUNT ext4 defaults,nofail,noatime,x-systemd.device-timeout=10s 0 2" >> /etc/fstab
fi
systemctl daemon-reload
mount "$MOUNT"
chown "$OWNER:$OWNER" "$MOUNT"
sudo -u "$OWNER" sh -c "echo 'Sicherungsziel EMS Keltenstr. 21 – eingerichtet $(date -Is)' > $MOUNT/.ems-backup-ziel"

echo
echo "Fertig: $PART (UUID $UUID) ist unter $MOUNT eingehängt."
df -h "$MOUNT"
