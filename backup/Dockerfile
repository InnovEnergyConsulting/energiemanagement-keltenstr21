# EMS-Sicherung: influx-CLI (konsistentes DB-Backup) + restic (verschlüsselt, dedupliziert)
# + rclone (OneDrive). Feste Versionen, keine Paketquellen-Überraschungen.
FROM restic/restic:0.18.1 AS restic
FROM rclone/rclone:1.71.2 AS rclone

FROM influxdb:2.7.12
COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
# restic darf als normaler Nutzer alle Dateien LESEN (Broker-/Caddy-Dateien gehören anderen UIDs),
# aber nichts schreiben – statt den ganzen Container als root laufen zu lassen.
RUN apt-get update && apt-get install -y --no-install-recommends libcap2-bin && \
    setcap cap_dac_read_search=+ep /usr/local/bin/restic && \
    apt-get purge -y libcap2-bin && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY backup/backup.sh /usr/local/bin/ems-backup
RUN chmod 755 /usr/local/bin/ems-backup
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/ems-backup"]
CMD ["daemon"]
