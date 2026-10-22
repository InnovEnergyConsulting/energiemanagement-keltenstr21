package de.keltenstr21.zaehlerpin;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.os.Bundle;
import android.os.SystemClock;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * Blinkt mit der Kamera-LED die PIN in den optischen Sensor eines Iskra MT691 und schaltet Menüpunkte (InF, Pin).
 * Zeiten laut Herstellerbeschreibung: kurz = Licht kürzer als 4 s; lang = länger als 5 s; nächste PIN-Stelle nach
 * 3 s ohne Licht. Alle Zeiten sind einstellbar und werden gespeichert.
 */
public class MainActivity extends Activity {

    // Feld-Schlüssel, Beschriftung, Vorgabe (ms bzw. Anzahl), erlaubter Bereich
    private static final String[][] FELDER = {
            {"kurz", "Kurzer Impuls – Licht an (ms)", "500", "100", "3500"},
            {"pauseKurz", "Pause zwischen kurzen Impulsen (ms)", "700", "200", "2500"},
            {"pauseZiffer", "Pause bis zur nächsten PIN-Stelle (ms, > 3000)", "4000", "3200", "15000"},
            {"lang", "Langer Impuls – Licht an (ms, > 5000)", "6500", "5200", "15000"},
            {"displaytest", "Nach Weckimpuls: Displaytest abwarten (ms)", "4000", "500", "15000"},
            {"einstieg", "Nach Einstiegsimpuls warten (ms)", "1500", "300", "2800"},
            {"start", "Startverzögerung zum Ausrichten (ms)", "3000", "0", "15000"},
            {"nKurz", "Anzahl für „N × kurz“ (z. B. 13 bis „InF“)", "13", "1", "40"},
    };

    private CameraManager cameraManager;
    private String torchId;
    private SharedPreferences prefs;
    private final EditText[] eingaben = new EditText[FELDER.length];
    private EditText pinFeld;
    private TextView pinAnzeige;
    private TextView lampe, status;
    private volatile boolean laeuft;
    private volatile boolean dauerLicht;
    private Thread worker;

    private static class Abbruch extends Exception {}

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        prefs = getSharedPreferences("einstellungen", MODE_PRIVATE);
        cameraManager = (CameraManager) getSystemService(CAMERA_SERVICE);
        try {
            for (String id : cameraManager.getCameraIdList()) {
                Boolean blitz = cameraManager.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
                if (blitz != null && blitz) { torchId = id; break; }
            }
        } catch (Exception e) {
            torchId = null;
        }
        setContentView(baueOberflaeche());
        if (torchId == null) zeigeStatus("Dieses Handy hat keine steuerbare Taschenlampe.");
    }

    // ------------------------------------------------------------------ Oberfläche
    private View baueOberflaeche() {
        float dp = getResources().getDisplayMetrics().density;
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * dp);
        root.setPadding(pad, pad, pad, pad);

        TextView titel = new TextView(this);
        titel.setText("Zähler-PIN (Iskra MT691)");
        titel.setTextSize(22);
        titel.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(titel);
        TextView hinweis = new TextView(this);
        hinweis.setText("LED des Handys dicht und gerade auf den optischen Sensor des Zählers halten (oben rechts, "
                + "Lesekopf ggf. abnehmen). Nicht die Prüfdiode „10 000 Imp./kWh“. Display beim Ablauf beobachten.");
        hinweis.setTextSize(13);
        hinweis.setPadding(0, (int) (4 * dp), 0, (int) (12 * dp));
        root.addView(hinweis);

        lampe = new TextView(this);
        lampe.setTextSize(28);
        lampe.setTypeface(Typeface.DEFAULT_BOLD);
        lampe.setGravity(Gravity.CENTER);
        lampe.setPadding(0, (int) (14 * dp), 0, (int) (14 * dp));
        root.addView(lampe);
        zeigeLampe(false);

        status = new TextView(this);
        status.setTextSize(16);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, (int) (8 * dp), 0, (int) (12 * dp));
        status.setText("Bereit.");
        root.addView(status);

        root.addView(label("PIN (Ziffern, meist 4 Stellen)", dp));
        pinFeld = new EditText(this);
        pinFeld.setInputType(InputType.TYPE_CLASS_NUMBER);          // sichtbar: Abgleich mit dem PIN-Brief
        pinFeld.setTextSize(22);
        pinFeld.setText(prefs.getString("pin", ""));
        root.addView(pinFeld);
        pinAnzeige = new TextView(this);
        pinAnzeige.setTextSize(30);
        pinAnzeige.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        pinAnzeige.setGravity(Gravity.CENTER);
        root.addView(pinAnzeige);
        zeigePin();
        pinFeld.addTextChangedListener(new android.text.TextWatcher() {
            public void beforeTextChanged(CharSequence c, int a, int b, int d) {}
            public void onTextChanged(CharSequence c, int a, int b, int d) {}
            public void afterTextChanged(android.text.Editable e) { zeigePin(); }
        });

        root.addView(knopf("PIN eingeben (automatisch)", v -> starte("pin"), dp));
        TextView ablauf = new TextView(this);
        ablauf.setText("Ablauf: Weckimpuls → Displaytest → Einstiegsimpuls → je Ziffer n kurze Impulse (0 = keiner) → "
                + "Pause → nächste Ziffer. Danach im Display prüfen, ob die PIN angenommen wurde.");
        ablauf.setTextSize(12);
        root.addView(ablauf);

        root.addView(label("Menü schrittweise (Display beobachten!)", dp));
        LinearLayout reihe = new LinearLayout(this);
        reihe.setOrientation(LinearLayout.HORIZONTAL);
        reihe.addView(knopf("1 × kurz", v -> starte("kurz1"), dp), gewicht());
        reihe.addView(knopf("N × kurz", v -> starte("kurzN"), dp), gewicht());
        reihe.addView(knopf("1 × lang", v -> starte("lang1"), dp), gewicht());
        root.addView(reihe);
        TextView menue = new TextView(this);
        menue.setText("Nach angenommener PIN: N × kurz bis „InF OFF“ → 1 × lang → „InF on“ → 1 × kurz → „Pin on“ → "
                + "1 × lang → „Pin OFF“. Nur lang leuchten, wenn der gewünschte Punkt wirklich angezeigt wird – bei "
                + "„E Clr“/„HIS Clr“ NIE lang (löscht Verbrauchsspeicher).");
        menue.setTextSize(12);
        root.addView(menue);

        LinearLayout reihe2 = new LinearLayout(this);
        reihe2.setOrientation(LinearLayout.HORIZONTAL);
        reihe2.addView(knopf("Licht an/aus (Ausrichten)", v -> umschaltenDauerlicht(), dp), gewicht());
        Button stopp = knopf("STOPP", v -> stoppe("Abgebrochen."), dp);
        stopp.setBackgroundColor(Color.rgb(179, 54, 47));
        stopp.setTextColor(Color.WHITE);
        reihe2.addView(stopp, gewicht());
        root.addView(reihe2);

        root.addView(label("Zeiten (werden gespeichert)", dp));
        for (int i = 0; i < FELDER.length; i++) {
            root.addView(label(FELDER[i][1], dp));
            EditText e = new EditText(this);
            e.setInputType(InputType.TYPE_CLASS_NUMBER);
            e.setText(prefs.getString(FELDER[i][0], FELDER[i][2]));
            eingaben[i] = e;
            root.addView(e);
        }
        root.addView(knopf("Zeiten auf Vorgabe zurücksetzen", v -> {
            for (int i = 0; i < FELDER.length; i++) eingaben[i].setText(FELDER[i][2]);
        }, dp));

        ScrollView sv = new ScrollView(this);
        sv.addView(root);
        return sv;
    }

    /** PIN groß und mit Abständen, Ziffer für Ziffer mit dem Brief des Netzbetreibers vergleichbar. */
    private void zeigePin() {
        String p = pinFeld.getText().toString().trim();
        StringBuilder b = new StringBuilder();
        for (char c : p.toCharArray()) b.append(c).append(' ');
        pinAnzeige.setText(p.isEmpty() ? "PIN: –" : "PIN: " + b.toString().trim() + "  (" + p.length() + " Stellen)");
    }

    private TextView label(String text, float dp) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(0, (int) (14 * dp), 0, (int) (2 * dp));
        return t;
    }

    private Button knopf(String text, View.OnClickListener l, float dp) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        return b;
    }

    private LinearLayout.LayoutParams gewicht() {
        return new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
    }

    private void zeigeLampe(boolean an) {
        runOnUiThread(() -> {
            lampe.setText(an ? "LICHT AN" : "aus");
            lampe.setBackgroundColor(an ? Color.rgb(255, 214, 0) : Color.rgb(60, 66, 72));
            lampe.setTextColor(an ? Color.BLACK : Color.rgb(200, 205, 210));
        });
    }

    private void zeigeStatus(String s) {
        runOnUiThread(() -> status.setText(s));
    }

    // ------------------------------------------------------------------ Einstellungen
    private long wert(String key) {
        for (int i = 0; i < FELDER.length; i++) {
            if (FELDER[i][0].equals(key)) return Long.parseLong(eingaben[i].getText().toString().trim());
        }
        throw new IllegalArgumentException(key);
    }

    /** Prüft alle Felder gegen die erlaubten Bereiche (sonst würde z. B. eine zu lange Pause als neue Ziffer gelten). */
    private String pruefeUndSpeichere() {
        SharedPreferences.Editor ed = prefs.edit();
        for (int i = 0; i < FELDER.length; i++) {
            String txt = eingaben[i].getText().toString().trim();
            long v;
            try { v = Long.parseLong(txt); } catch (NumberFormatException e) { return FELDER[i][1] + ": keine Zahl"; }
            long min = Long.parseLong(FELDER[i][3]), max = Long.parseLong(FELDER[i][4]);
            if (v < min || v > max) return FELDER[i][1] + ": erlaubt " + min + " – " + max;
            ed.putString(FELDER[i][0], txt);
        }
        ed.putString("pin", pinFeld.getText().toString().trim());
        ed.apply();
        return null;
    }

    // ------------------------------------------------------------------ Abläufe
    private void starte(String was) {
        if (torchId == null) { zeigeStatus("Keine steuerbare Taschenlampe gefunden."); return; }
        if (laeuft) { zeigeStatus("Läuft bereits – erst STOPP."); return; }
        String fehler = pruefeUndSpeichere();
        if (fehler != null) { zeigeStatus(fehler); return; }
        String pin = pinFeld.getText().toString().trim();
        if (was.equals("pin") && !pin.matches("\\d{1,8}")) { zeigeStatus("Bitte PIN aus Ziffern eingeben."); return; }
        dauerLicht = false;
        laeuft = true;
        worker = new Thread(() -> {
            try {
                countdown(wert("start"));
                switch (was) {
                    case "pin": pinEingeben(pin); break;
                    case "kurz1": kurzeImpulse(1); zeigeStatus("1 × kurz gesendet – Display prüfen."); break;
                    case "kurzN": kurzeImpulse((int) wert("nKurz")); zeigeStatus(wert("nKurz") + " × kurz gesendet – Display prüfen."); break;
                    case "lang1":
                        zeigeStatus("Langer Impuls …");
                        impuls(wert("lang"));
                        zeigeStatus("1 × lang gesendet – Display prüfen.");
                        break;
                }
            } catch (Abbruch a) {
                // Status wurde von stoppe() gesetzt
            } finally {
                licht(false);
                laeuft = false;
            }
        });
        worker.start();
    }

    private void pinEingeben(String pin) throws Abbruch {
        zeigeStatus("Weckimpuls …");
        impuls(wert("kurz"));
        warteMitAnzeige(wert("displaytest"), "Displaytest abwarten");
        zeigeStatus("Einstiegsimpuls (PIN-Abfrage öffnen) …");
        impuls(wert("kurz"));
        warte(wert("einstieg"));
        for (int i = 0; i < pin.length(); i++) {
            int ziffer = pin.charAt(i) - '0';
            for (int k = 1; k <= ziffer; k++) {
                zeigeStatus("Stelle " + (i + 1) + " von " + pin.length() + " (Ziffer " + ziffer + "): Impuls " + k + " / " + ziffer);
                impuls(wert("kurz"));
                if (k < ziffer) warte(wert("pauseKurz"));
            }
            warteMitAnzeige(wert("pauseZiffer"), "Stelle " + (i + 1) + " fertig (" + (ziffer == 0 ? "0 = kein Impuls" : ziffer + " Impulse")
                    + ") – warten");
        }
        zeigeStatus("PIN gesendet. Display prüfen: angenommen? Dann „N × kurz“ bis „InF“.");
    }

    private void kurzeImpulse(int n) throws Abbruch {
        for (int k = 1; k <= n; k++) {
            zeigeStatus("Kurzer Impuls " + k + " / " + n);
            impuls(wert("kurz"));
            if (k < n) warte(wert("pauseKurz"));
        }
    }

    private void countdown(long ms) throws Abbruch {
        long ende = SystemClock.elapsedRealtime() + ms;
        while (SystemClock.elapsedRealtime() < ende) {
            long rest = ende - SystemClock.elapsedRealtime();
            zeigeStatus("Start in " + ((rest + 999) / 1000) + " s – Handy ausrichten");
            warte(Math.min(200, rest));
        }
    }

    private void warteMitAnzeige(long ms, String text) throws Abbruch {
        long ende = SystemClock.elapsedRealtime() + ms;
        while (SystemClock.elapsedRealtime() < ende) {
            long rest = ende - SystemClock.elapsedRealtime();
            zeigeStatus(text + " … " + String.format(java.util.Locale.GERMANY, "%.1f s", rest / 1000.0));
            warte(Math.min(100, rest));
        }
    }

    private void impuls(long ms) throws Abbruch {
        licht(true);
        warte(ms);
        licht(false);
    }

    /** Wartet genau (Uhr statt Schlafsumme) und reagiert auf STOPP. */
    private void warte(long ms) throws Abbruch {
        long ende = SystemClock.elapsedRealtime() + ms;
        while (true) {
            if (!laeuft) throw new Abbruch();
            long rest = ende - SystemClock.elapsedRealtime();
            if (rest <= 0) return;
            try { Thread.sleep(Math.min(rest, 20)); } catch (InterruptedException e) { throw new Abbruch(); }
        }
    }

    private void licht(boolean an) {
        try {
            cameraManager.setTorchMode(torchId, an);
            zeigeLampe(an);
        } catch (Exception e) {
            zeigeStatus("Taschenlampe nicht schaltbar: " + e.getMessage());
        }
    }

    private void umschaltenDauerlicht() {
        if (torchId == null || laeuft) return;
        dauerLicht = !dauerLicht;
        licht(dauerLicht);
        zeigeStatus(dauerLicht ? "Licht dauerhaft an – zum Ausrichten. Nochmal tippen zum Ausschalten." : "Bereit.");
    }

    private void stoppe(String text) {
        laeuft = false;
        dauerLicht = false;
        if (worker != null) worker.interrupt();
        licht(false);
        zeigeStatus(text);
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (laeuft || dauerLicht) stoppe("Unterbrochen (App verlassen).");
    }
}
