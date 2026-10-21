// ems-eebus – EEBUS-Adapter des EMS Keltenstr. 21 (Stufe A: anbinden & mitlesen).
//
// Konzept: docs/EEBUS_Adapter_Konzept.md
//   - vertraut AUSSCHLIESSLICH der konfigurierten SKI (Vaillant VR 940)
//   - meldet nur Überwachungs-Anwendungsfälle an (MA-MPC Leistung, MA-MDT Warmwasser) und liest/abonniert
//     zusätzlich alle lesbaren Features (Messung, Sollwert, HVAC, Diagnose …) nur lesend für die Analyse –
//     keine steuernden (OHPCF, LPC), damit das EMS nicht ungewollt die Regelung übernimmt
//   - zeichnet jede SHIP/SPINE-Nachricht (spine-trace-<tag>.jsonl) und die Gerätebeschreibung
//     (discovery-<zeit>.json) für die Datenstromanalyse auf
//   - gibt Messwerte und Ereignisse als Rohdaten nach ems/v1/raw/eebus/<strom>
package main

import (
	"crypto/ecdsa"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	"github.com/enbility/eebus-go/api"
	"github.com/enbility/eebus-go/service"
	"github.com/enbility/eebus-go/usecases/ma/mdt"
	"github.com/enbility/eebus-go/usecases/ma/mpc"
	shipapi "github.com/enbility/ship-go/api"
	"github.com/enbility/ship-go/cert"
	spineapi "github.com/enbility/spine-go/api"
	"github.com/enbility/spine-go/model"
)

func env(name, def string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return def
}

func nowISO() string { return time.Now().UTC().Format("2006-01-02T15:04:05.000Z") }

// ------------------------------------------------------------------ Zertifikat (= eigene SKI, dauerhaft)

func loadOrCreateCert(dir string) (tls.Certificate, string) {
	crt, key := filepath.Join(dir, "cert.pem"), filepath.Join(dir, "key.pem")
	c, err := tls.LoadX509KeyPair(crt, key)
	if err != nil {
		log.Printf("Kein Zertifikat in %s – erzeuge neues (neue SKI, Kopplung nötig)", dir)
		c, err = cert.CreateCertificate("EMS", "InnovEnergy Keltenstr21", "DE", "EMS-Keltenstr21-01")
		if err != nil {
			log.Fatal(err)
		}
		pemCert := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: c.Certificate[0]})
		b, err := x509.MarshalECPrivateKey(c.PrivateKey.(*ecdsa.PrivateKey))
		if err != nil {
			log.Fatal(err)
		}
		pemKey := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: b})
		if err := os.WriteFile(crt, pemCert, 0o600); err != nil {
			log.Fatal(err)
		}
		if err := os.WriteFile(key, pemKey, 0o600); err != nil {
			log.Fatal(err)
		}
	}
	leaf, err := x509.ParseCertificate(c.Certificate[0])
	if err != nil {
		log.Fatal(err)
	}
	ski, err := cert.SkiFromCertificate(leaf)
	if err != nil {
		log.Fatal(err)
	}
	return c, ski
}

// ------------------------------------------------------------------ Aufzeichnung (JSONL, je Tag eine Datei)

type recorder struct {
	mu   sync.Mutex
	dir  string
	day  string
	file *os.File
	keep int
}

func (r *recorder) write(rec map[string]any) {
	r.mu.Lock()
	defer r.mu.Unlock()
	day := time.Now().Format("2006-01-02")
	if r.file == nil || day != r.day {
		if r.file != nil {
			_ = r.file.Close()
		}
		f, err := os.OpenFile(filepath.Join(r.dir, "spine-trace-"+day+".jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o640)
		if err != nil {
			log.Printf("Trace-Datei nicht schreibbar: %v", err)
			return
		}
		r.file, r.day = f, day
		r.cleanup()
	}
	b, _ := json.Marshal(rec)
	_, _ = r.file.Write(append(b, '\n'))
}

func (r *recorder) cleanup() { // nur die letzten <keep> Tage behalten
	files, _ := filepath.Glob(filepath.Join(r.dir, "spine-trace-*.jsonl"))
	sort.Strings(files)
	for len(files) > r.keep {
		_ = os.Remove(files[0])
		files = files[1:]
	}
}

// ------------------------------------------------------------------ Adapter

type adapter struct {
	svc        *service.Service
	remoteSKI  string
	mq         mqtt.Client
	rec        *recorder
	dataDir    string
	mpc        *mpc.MPC
	mdt        *mdt.MDT
	lastDisc   string
	debug      bool
	local      spineapi.EntityLocalInterface
	subMu      sync.Mutex
	subscribed map[string]bool
	availMu    sync.Mutex
	lastStatus string // zuletzt gemeldeter Verfügbarkeitsstatus (Meldungen nur bei Änderung)
	connected  bool
	changedAt  time.Time // letzter Wechsel verbunden/getrennt (für die Zeitüberwachung)
}

// Feature-Typen, die für die Analyse NUR GELESEN und abonniert werden (kein Write, kein Binding,
// kein zusätzlicher Anwendungsfall -> die WP sieht das EMS weiterhin nur als Beobachter).
var readTypes = []model.FeatureTypeType{
	model.FeatureTypeTypeMeasurement, model.FeatureTypeTypeSetpoint, model.FeatureTypeTypeHvac,
	model.FeatureTypeTypeDeviceDiagnosis, model.FeatureTypeTypeDeviceClassification,
	model.FeatureTypeTypeElectricalConnection, model.FeatureTypeTypeLoadControl,
	model.FeatureTypeTypeDeviceConfiguration,
}

func isReadType(t model.FeatureTypeType) bool {
	for _, x := range readTypes {
		if x == t {
			return true
		}
	}
	return false
}

// readAll: alle lesbaren Server-Features des VR 940 abonnieren (einmal je Verbindung) und auslesen.
// Die Antworten landen über die Trace-Aufzeichnung vollständig in spine-trace-<Tag>.jsonl.
func (a *adapter) readAll(ski string) {
	dev := a.svc.LocalDevice().RemoteDeviceForSki(ski)
	if dev == nil {
		return
	}
	reads, subs := 0, 0
	for _, e := range dev.Entities() {
		for _, f := range e.Features() {
			if f.Role() != model.RoleTypeServer || !isReadType(f.Type()) || f.Address() == nil {
				continue
			}
			lf := a.local.GetOrAddFeature(f.Type(), model.RoleTypeClient)
			key := fmt.Sprintf("%s/%s/%v", ski, addr(e), *f.Address().Feature)
			a.subMu.Lock()
			if !a.subscribed[key] {
				if _, err := lf.SubscribeToRemote(f.Address()); err == nil {
					a.subscribed[key] = true
					subs++
				}
			}
			a.subMu.Unlock()
			// erst Beschreibungen, dann Werte – sonst kommen Werte an, deren Bedeutung der Normalizer noch nicht kennt
			for pass := 0; pass < 2; pass++ {
				for fn, op := range f.Operations() {
					isDesc := strings.Contains(string(fn), "Description")
					if !op.Read() || (pass == 0) != isDesc {
						continue
					}
					if _, err := lf.RequestRemoteData(fn, nil, nil, f); err == nil {
						reads++
					}
				}
			}
		}
	}
	log.Printf("Analyse-Lesung: %d Leseanfragen, %d neue Abos", reads, subs)
}

func (a *adapter) publish(topic string, payload any, retain bool) {
	if a.mq == nil || !a.mq.IsConnected() {
		return
	}
	b, _ := json.Marshal(payload)
	a.mq.Publish(topic, 1, retain, b)
}

func (a *adapter) availability(status, reason string) {
	a.availMu.Lock()
	a.lastStatus = status + "|" + reason
	a.availMu.Unlock()
	a.publish("ems/v1/availability/eebus", map[string]any{"status": status, "since": nowISO(), "reason": reason}, true)
}

// unreachable wird aus den Fehlermeldungen von ship-go abgeleitet ("connection to <ski> at ... failed: dial tcp ...").
// Gemeldet wird nur bei Zustandswechsel, damit die Wiederholversuche nicht jedes Mal eine Meldung erzeugen.
func (a *adapter) unreachable(msg string) {
	if !strings.Contains(msg, a.remoteSKI) || !strings.Contains(msg, "failed") {
		return
	}
	cause := msg
	if i := strings.LastIndex(msg, ": "); i >= 0 {
		cause = msg[i+2:]
	}
	reason := "VR 940 im Netz nicht erreichbar (" + cause + ")"
	a.availMu.Lock()
	same := a.lastStatus == "offline|"+reason
	a.availMu.Unlock()
	if !same {
		a.availability("offline", reason)
	}
}

func (a *adapter) raw(stream string, payload any) {
	a.publish("ems/v1/raw/eebus/"+stream, map[string]any{
		"source": "eebus", "stream": stream, "received_at": nowISO(), "http_status": 0, "duration_ms": 0,
		"payload": payload}, false)
}

// --- Logging-Schnittstelle von eebus-go: Send:/Recv: enthalten die vollständige SHIP/SPINE-Nachricht

func (a *adapter) Trace(args ...interface{}) {
	if len(args) >= 3 {
		if dir, ok := args[0].(string); ok && (dir == "Send:" || dir == "Recv:") {
			text := fmt.Sprint(args[2])
			rec := map[string]any{"ts": nowISO(), "dir": strings.TrimSuffix(dir, ":"), "ski": fmt.Sprint(args[1])}
			var parsed any
			if json.Unmarshal([]byte(text), &parsed) == nil {
				rec["msg"] = parsed
				// Stufe C: jede empfangene Daten-Nachricht (Antwort/Benachrichtigung mit *ListData) unverändert
				// an das EMS geben – die fachliche Zuordnung macht der Normalizer (Mapping eebus_vr940.yaml).
				if dir == "Recv:" && strings.Contains(text, "ListData") &&
					(strings.Contains(text, `"reply"`) || strings.Contains(text, `"notify"`)) {
					a.raw("spine", parsed)
				}
			} else {
				rec["text"] = text // SHIP-Steuernachrichten sind teils kein reines JSON
			}
			a.rec.write(rec)
			return
		}
	}
	if a.debug {
		log.Println(append([]interface{}{"TRACE"}, args...)...)
	}
}
func (a *adapter) Tracef(f string, args ...interface{}) { a.Trace(fmt.Sprintf(f, args...)) }
func (a *adapter) Debug(args ...interface{}) {
	if a.debug {
		log.Println(append([]interface{}{"DEBUG"}, args...)...)
	}
}
func (a *adapter) Debugf(f string, args ...interface{}) { a.Debug(fmt.Sprintf(f, args...)) }
func (a *adapter) Info(args ...interface{})             { log.Println(append([]interface{}{"INFO"}, args...)...) }
func (a *adapter) Infof(f string, args ...interface{})  { log.Printf("INFO "+f, args...) }
func (a *adapter) Error(args ...interface{}) {
	log.Println(append([]interface{}{"ERROR"}, args...)...)
	a.unreachable(fmt.Sprint(args...))
}
func (a *adapter) Errorf(f string, args ...interface{}) {
	log.Printf("ERROR "+f, args...)
	a.unreachable(fmt.Sprintf(f, args...))
}

// --- ServiceReaderInterface

func (a *adapter) setConnected(c bool) {
	a.availMu.Lock()
	a.connected, a.changedAt = c, time.Now()
	a.availMu.Unlock()
}

// watchdog: ship-go meldet ein ausgeschaltetes Gerät nicht zuverlässig als Fehler (Verbindungsversuche laufen ins
// Leere). Steht nach 5 min keine Verbindung, gilt der VR 940 als nicht erreichbar (21.10.2026, VR 940 ausgeschaltet).
func (a *adapter) watchdog() {
	for range time.Tick(time.Minute) {
		a.availMu.Lock()
		down, since, last := !a.connected, a.changedAt, a.lastStatus
		a.availMu.Unlock()
		if down && time.Since(since) > 5*time.Minute && !strings.HasPrefix(last, "offline|") {
			a.availability("offline", fmt.Sprintf("VR 940 seit %s nicht erreichbar (ausgeschaltet oder Netzwerk)",
				since.Local().Format("15:04")))
		}
	}
}

func (a *adapter) RemoteServiceConnected(s api.ServiceInterface, id shipapi.ServiceIdentity) {
	a.setConnected(true)
	log.Printf("Verbunden mit %s", id.SKI)
	a.availability("online", "SHIP-Verbindung steht")
}
func (a *adapter) RemoteServiceDisconnected(s api.ServiceInterface, id shipapi.ServiceIdentity) {
	a.setConnected(false)
	log.Printf("Getrennt von %s", id.SKI)
	a.subMu.Lock()
	a.subscribed = map[string]bool{} // nach Neuverbindung neu abonnieren
	a.subMu.Unlock()
	a.availability("offline", "SHIP-Verbindung getrennt")
}
func (a *adapter) VisibleRemoteMdnsServicesUpdated(s api.ServiceInterface, entries []shipapi.RemoteMdnsService) {
	list := []map[string]any{}
	for _, e := range entries {
		list = append(list, map[string]any{"name": e.Name, "ski": e.Ski, "ship_id": e.ShipID, "brand": e.Brand,
			"model": e.Model, "type": e.Type, "serial": e.Serial, "categories": e.Categories})
	}
	a.raw("mdns", list)
}
func (a *adapter) ServiceUpdated(id shipapi.ServiceIdentity) {}
func (a *adapter) ServicePairingDetailUpdate(id shipapi.ServiceIdentity, d *shipapi.ConnectionStateDetail) {
	names := map[shipapi.ConnectionState]string{
		shipapi.ConnectionStateNone: "keine", shipapi.ConnectionStateQueued: "eingereiht",
		shipapi.ConnectionStateInitiated: "eingeleitet", shipapi.ConnectionStateReceivedPairingRequest: "Anfrage erhalten",
		shipapi.ConnectionStateInProgress: "Handshake läuft", shipapi.ConnectionStateTrusted: "vertraut",
		shipapi.ConnectionStatePin: "PIN", shipapi.ConnectionStateCompleted: "abgeschlossen",
		shipapi.ConnectionStateRemoteDeniedTrust: "Vertrauen verweigert", shipapi.ConnectionStateError: "Fehler"}
	state := names[d.State()]
	log.Printf("Kopplungszustand %s: %s", id.SKI, state)
	switch d.State() {
	case shipapi.ConnectionStateCompleted:
		a.setConnected(true)
		a.availability("online", "Kopplung abgeschlossen")
	case shipapi.ConnectionStateTrusted:
		// Zwischenstand – "online" kommt mit Completed bzw. RemoteServiceConnected
	default:
		a.publish("ems/v1/availability/eebus", map[string]any{"status": "pairing", "since": nowISO(),
			"reason": "Kopplungszustand: " + state}, true)
	}
	if id.SKI == a.remoteSKI && d.State() == shipapi.ConnectionStateRemoteDeniedTrust {
		log.Printf("VR 940 hat das Vertrauen verweigert – Kopplung in der myVAILLANT-App bestätigen")
	}
}
func (a *adapter) AllowWaitingForTrust(id shipapi.ServiceIdentity) bool { return id.SKI == a.remoteSKI }
func (a *adapter) ServiceAutoTrusted(s api.ServiceInterface, id shipapi.ServiceIdentity) {
	log.Printf("Vertrauen hergestellt: %s", id.SKI)
}
func (a *adapter) ServiceAutoTrustFailed(s api.ServiceInterface, id shipapi.ServiceIdentity, reason error) {
	log.Printf("Vertrauen fehlgeschlagen %s: %v", id.SKI, reason)
}
func (a *adapter) ServiceAutoTrustRemoved(s api.ServiceInterface, id shipapi.ServiceIdentity, reason string) {
	log.Printf("Vertrauen entfernt %s: %s", id.SKI, reason)
}

// --- Anwendungsfälle (nur lesend)

func addr(e spineapi.EntityRemoteInterface) string {
	if e == nil || e.Address() == nil {
		return ""
	}
	parts := []string{}
	for _, x := range e.Address().Entity {
		parts = append(parts, strconv.Itoa(int(x)))
	}
	return strings.Join(parts, ".")
}

func errStr(err error) any {
	if err == nil {
		return nil
	}
	return err.Error()
}

func (a *adapter) onMPC(ski string, dev spineapi.DeviceRemoteInterface, e spineapi.EntityRemoteInterface, ev api.EventType) {
	// Beim Entfernen des Geräts (Verbindungsabbruch) ruft eebus-go die Ereignisfunktion ohne Entität auf -> kein Zugriff,
	// sonst Absturz (nil pointer, 21.10.2026). recover als zweite Sicherung: ein Ereignis darf den Adapter nie beenden.
	defer func() {
		if r := recover(); r != nil {
			log.Printf("Ereignis %s abgefangen: %v", ev, r)
		}
	}()
	if e == nil || e.Address() == nil {
		log.Printf("Ereignis %s ohne Entität (Gerät getrennt) – ignoriert", ev)
		return
	}
	out := map[string]any{"event": string(ev), "entity": addr(e), "entity_type": string(e.EntityType())}
	if v, err := a.mpc.Power(e); err == nil {
		out["power_w"] = v
	} else {
		out["power_error"] = errStr(err)
	}
	if v, err := a.mpc.PowerPerPhase(e); err == nil {
		out["power_per_phase_w"] = v
	}
	if v, err := a.mpc.EnergyConsumed(e); err == nil {
		out["energy_consumed_wh"] = v
	}
	if v, err := a.mpc.EnergyProduced(e); err == nil {
		out["energy_produced_wh"] = v
	}
	if v, err := a.mpc.CurrentPerPhase(e); err == nil {
		out["current_per_phase_a"] = v
	}
	if v, err := a.mpc.VoltagePerPhase(e); err == nil {
		out["voltage_per_phase_v"] = v
	}
	if v, err := a.mpc.Frequency(e); err == nil {
		out["frequency_hz"] = v
	}
	a.raw("mpc", out)
	a.dumpDiscovery(ski)
}

func (a *adapter) onMDT(ski string, dev spineapi.DeviceRemoteInterface, e spineapi.EntityRemoteInterface, ev api.EventType) {
	// Beim Entfernen des Geräts (Verbindungsabbruch) ruft eebus-go die Ereignisfunktion ohne Entität auf -> kein Zugriff,
	// sonst Absturz (nil pointer, 21.10.2026). recover als zweite Sicherung: ein Ereignis darf den Adapter nie beenden.
	defer func() {
		if r := recover(); r != nil {
			log.Printf("Ereignis %s abgefangen: %v", ev, r)
		}
	}()
	if e == nil || e.Address() == nil {
		log.Printf("Ereignis %s ohne Entität (Gerät getrennt) – ignoriert", ev)
		return
	}
	out := map[string]any{"event": string(ev), "entity": addr(e), "entity_type": string(e.EntityType())}
	if v, err := a.mdt.Temperature(e, model.UnitOfMeasurementTypedegC); err == nil {
		out["temperature_degc"] = v
	} else {
		out["temperature_error"] = errStr(err)
	}
	a.raw("mdt", out)
	a.dumpDiscovery(ski)
}

// --- Gerätebeschreibung (Entitäten, Features, Funktionen, Anwendungsfälle) für die Analyse

func (a *adapter) dumpDiscovery(ski string) {
	dev := a.svc.LocalDevice().RemoteDeviceForSki(ski)
	if dev == nil {
		return
	}
	entities := []map[string]any{}
	for _, e := range dev.Entities() {
		feats := []map[string]any{}
		for _, f := range e.Features() {
			ops := map[string]string{}
			for fn, op := range f.Operations() {
				ops[string(fn)] = fmt.Sprint(op)
			}
			fd := map[string]any{"type": string(f.Type()), "role": string(f.Role()), "functions": ops}
			if f.Address() != nil && f.Address().Feature != nil {
				fd["feature_id"] = int(*f.Address().Feature)
			}
			if d := f.Description(); d != nil {
				fd["description"] = string(*d)
			}
			feats = append(feats, fd)
		}
		ed := map[string]any{"address": addr(e), "type": string(e.EntityType()), "features": feats}
		if d := e.Description(); d != nil {
			ed["description"] = string(*d)
		}
		entities = append(entities, ed)
	}
	devType := ""
	if dev.DeviceType() != nil {
		devType = string(*dev.DeviceType())
	}
	disc := map[string]any{"ski": ski, "device_type": devType, "entities": entities, "use_cases": dev.UseCases()}
	b, _ := json.MarshalIndent(disc, "", " ")
	if string(b) == a.lastDisc {
		return
	}
	a.lastDisc = string(b)
	name := filepath.Join(a.dataDir, "discovery-"+time.Now().Format("20060102-150405")+".json")
	if err := os.WriteFile(name, b, 0o640); err == nil {
		log.Printf("Gerätebeschreibung aufgezeichnet: %s (%d Entitäten)", name, len(entities))
	}
	a.raw("discovery", disc)
	go a.readAll(ski)
}

// ------------------------------------------------------------------ Start

func main() {
	certDir := env("EMS_EEBUS_CERT_DIR", "/run/eebus-cert")
	dataDir := env("EMS_EEBUS_DATA_DIR", "/data")
	remoteSKI := strings.ToLower(env("EMS_EEBUS_REMOTE_SKI", ""))
	port, _ := strconv.Atoi(env("EMS_EEBUS_PORT", "4712"))
	if len(remoteSKI) != 40 {
		log.Fatal("EMS_EEBUS_REMOTE_SKI fehlt oder ist keine 40-stellige SKI")
	}
	certificate, ownSKI := loadOrCreateCert(certDir)
	_ = os.WriteFile(filepath.Join(dataDir, "eigene-ski.txt"), []byte(ownSKI+"\n"), 0o644)
	log.Printf("Eigene SKI: %s  (in der myVAILLANT-App bei der EEBUS-Kopplung vergleichen)", ownSKI)
	log.Printf("Vertraue ausschließlich: %s", remoteSKI)

	a := &adapter{remoteSKI: remoteSKI, dataDir: dataDir, debug: env("EMS_EEBUS_DEBUG", "") == "1",
		rec: &recorder{dir: dataDir, keep: 30}, subscribed: map[string]bool{}}

	// MQTT (Rohdaten + Verfügbarkeit); ohne Broker läuft die Aufzeichnung trotzdem weiter
	pw, _ := os.ReadFile(env("EMS_MQTT_PASSWORD_FILE", "/run/ems-secrets/mqtt_eebus_adapter"))
	opts := mqtt.NewClientOptions().AddBroker(env("EMS_MQTT_URL", "tcp://192.168.0.172:1883")).
		SetClientID("ems-eebus").SetUsername("eebus_adapter").SetPassword(strings.TrimSpace(string(pw))).
		SetAutoReconnect(true).SetMaxReconnectInterval(2*time.Minute).SetConnectRetry(true).
		SetWill("ems/v1/availability/eebus", `{"status":"offline","reason":"Adapter beendet"}`, 1, true)
	a.mq = mqtt.NewClient(opts)
	a.mq.Connect()

	conf, err := api.NewConfiguration("EMS", "InnovEnergy", "EMS-Keltenstr21", "EMS-Keltenstr21-01",
		[]shipapi.DeviceCategoryType{shipapi.DeviceCategoryTypeEnergyManagementSystem},
		model.DeviceTypeTypeEnergyManagementSystem,
		[]model.EntityTypeType{model.EntityTypeTypeCEM},
		port, certificate, 4*time.Second, nil, nil)
	if err != nil {
		log.Fatal(err)
	}
	conf.SetAlternateIdentifier("EMS-Keltenstr21-01")
	if iface := env("EMS_EEBUS_IFACE", ""); iface != "" {
		conf.SetInterfaces([]string{iface})
	}

	a.svc = service.NewService(conf, a)
	a.svc.SetLogging(a)
	if err := a.svc.Setup(); err != nil {
		log.Fatal(err)
	}
	local := a.svc.LocalDevice().EntityForType(model.EntityTypeTypeCEM)
	a.local = local
	// Client-Features VOR dem Start anlegen, damit sie in unserer eigenen Gerätebeschreibung stehen
	for _, t := range readTypes {
		local.GetOrAddFeature(t, model.RoleTypeClient)
	}
	a.mpc = mpc.NewMPC(local, a.onMPC)
	a.mdt = mdt.NewMDT(local, a.onMDT)
	for _, uc := range []api.UseCaseInterface{a.mpc, a.mdt} {
		if err := a.svc.AddUseCase(uc); err != nil {
			log.Fatal(err)
		}
	}
	a.svc.RegisterRemoteService(shipapi.NewServiceIdentity(remoteSKI, "", ""))
	if err := a.svc.Start(); err != nil {
		log.Fatal(err)
	}
	a.setConnected(false)
	a.availability("verbindet", "Adapter gestartet, baue Verbindung zum VR 940 auf")
	go a.watchdog()
	log.Printf("EEBUS-Adapter läuft (Port %d) – nur Überwachung: MA-MPC, MA-MDT", port)

	// Gerätebeschreibung regelmäßig prüfen (neue Entitäten/Features nach Firmware-Update)
	go func() {
		for range time.Tick(5 * time.Minute) {
			a.dumpDiscovery(remoteSKI)
			a.readAll(remoteSKI) // Werte ohne Notify (z. B. Beschreibungen) regelmäßig nachlesen
		}
	}()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	<-sig
	a.availability("offline", "Adapter beendet")
	a.svc.Shutdown()
	a.mq.Disconnect(500)
}
