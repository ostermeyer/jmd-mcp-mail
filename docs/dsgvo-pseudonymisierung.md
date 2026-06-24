# DSGVO-Pseudonymisierung in jmd-mcp-mail — Design-Spezifikation

Status: **Entwurf / Designkonsens** · Branch: `dsgvo` · Stand: 23. Juni 2026 · de-DE

> Diese Spec hält den mit dem Maintainer abgestimmten Designkonsens fest und dient
> als Brücke zur Implementierung. Sie beschreibt *Verhalten und Datenfluss*, noch
> keinen fertigen Code.

---

## 1. Zweck und Einordnung

Ziel: **personenbezogene E-Mail-Identitäten am Rand von `jmd-mcp-mail` minimieren,
bevor sie in den LLM-Kontext (und damit potenziell ins Drittland) gelangen.**

Wichtig für die Einordnung: Den rechtlichen Rahmen für die *Zulässigkeit* des
Transfers tragen AVV (Art. 28) + DPF/SCC (Kapitel V) + idealerweise EU-Residenz
(siehe [`claude-cowork-code-dsgvo-konformitaet.md`](claude-cowork-code-dsgvo-konformitaet.md)).
Die Pseudonymisierung ist **Datensparsamkeit und Defense-in-Depth** (Art. 5 Abs. 1
lit. c, Art. 25, Erwägungsgrund 28): Sie stärkt die Berechtigtes-Interesse-Abwägung
für die Daten Dritter und reduziert, was im Schadensfall (CLOUD-Act-Zugriff,
DPF-Wegfall) faktisch abfließt. Sie **muss die Rechtsgrundlage nicht allein tragen.**

**Abgrenzung:** Betrifft nur `jmd-mcp-mail`. `jmd-mcp-oauth2` transportiert keine
Mail-Inhalte und bleibt unberührt. Das Feature lebt ausschließlich auf dem Branch
`dsgvo`; `main` bleibt eigenständige OSS-Basis.

---

## 2. Schutzumfang (bewusst flach)

**Im Scope:**
- Adressen **und** Anzeigenamen in den Headern (`From`, `To`, `Cc`, `Reply-To`,
  `Sender`).
- E-Mail-Adressen, die per **Regex** in Betreff und Body auftauchen.

**Nicht im Scope:**
- Tiefe Body-NER (Namen, Telefonnummern, Anschriften im Fließtext). Begründung:
  niedrige Präzision, zerschießt genau den Text, den der Nutzer lesen/zusammenfassen
  will; schlechtes Kosten/Nutzen-Verhältnis. Der Rest-Body-Inhalt wird durch den
  rechtlichen Rahmen abgedeckt, nicht durch dieses Feature.

Das ist ein **bewusster Trade-off** (Usability ↔ Minimierung), bei Bedarf später
nachschärfbar.

---

## 3. Pseudonym-Form

Form im Transkript: **`Vorname <key>`**, z. B. `Alice <a1b2c3>`.

- **`<key>`** — deterministischer **Einweg-Token**: `HMAC-SHA256(secret, addr_norm)`,
  gekürzt auf ~6–8 Zeichen (Base32, klein­geschrieben). Nicht entschlüsselbar, nur
  **abgleichbar**. Über Sessions hinweg stabil (gleiche Adresse → gleicher Token),
  **ohne** dass eine Tabelle persistiert wird. Disambiguiert Gleichnamige
  (`Alice <a1b2c3>` vs. `Alice <d4e5f6>`).
- **`Vorname`** — aus dem Anzeigenamen extrahiert. Regel: **nur wenn eindeutig
  extrahierbar**, sonst reiner `<key>`. Konservativ bei unklaren Anzeigenamen:
  - `"Alice Schmidt"` → `Alice <key>`
  - `"Schmidt, Alice"`, Rollen-Adressen (`"Support Team"`), nicht-lateinische
    Schrift, **kein** Anzeigename → nur `<key>`
- Der Vorname ist selbst ein **Rest-PII-Fragment**, das weiterhin übermittelt wird —
  bewusst akzeptiert zugunsten der Lesbarkeit, ehrlich zu dokumentieren.

**Domain-Disambiguierung (opt-in, default aus):** optional ein Domain-Token, das
„gleiche Firma"-Beziehungen erhält (z. B. `Alice <key>@<dom>`).

---

## 4. Token-Verfahren & Secret

- **Secret:** 32 zufällige Bytes, im OS-Keystore (bestehendes Credential-Muster,
  Service `jmd-mcp-mail`). Beim ersten Bedarf einmalig erzeugt und gespeichert.
- Determinismus über das Secret → stabile Tokens ohne Mapping-Tabelle.
- Einwegigkeit → aus dem Token allein lässt sich die Adresse nicht rekonstruieren
  (man bräuchte zusätzlich die Mailbox **und** das Secret).
- **Normalisierung** der Adresse vor dem HMAC (lowercase, trim). Offen: ob
  Gmail-Punkte/`+tags` normalisiert werden (siehe §9).

---

## 5. Persistenz & Auflösung

- **Keine persistente Mapping-Tabelle treibt die Auflösung.** Persistiert wird für
  die Auflösung **nur** das Secret (Keystore); Token→Adresse läuft ausschließlich
  über die In-Memory-Map (s. u.).
- *(Einzige Tabelle at rest — rein menschenlesbar, **keine** Datenquelle für die
  Auflösung:)* der Server schreibt ein **Session-Transkript** `contacts.md` (der
  Re-ID-Schlüssel) ins Config-Verzeichnis. Es spiegelt nur die **laufende Session**
  und wird an den Session-Grenzen **gelöscht** — Details in §12.3.
- **Vorwärts** (echt → Pseudonym): on-the-fly beim Rendern.
- **Rückwärts** (Pseudonym → echt): **In-Memory-Map**, beim Lesen befüllt. Für einen
  Token, den der Server diese Session noch nicht gesehen hat, wird die Map durch
  erneutes Scannen des betroffenen Ordners rekonstruiert (Adressen hashen, Treffer
  suchen).
- Lebensdauer = Serverprozess; geht bei Neustart verloren und wird lazy neu
  aufgebaut. Konsequenz: **gestrige Transkript-Tokens sind nach Neustart nur via
  Mailbox-Scan auflösbar** — kein Reveal-Feature (siehe §10).

---

## 6. Datenfluss

### Ausgehend (Server → LLM) — beim `read`
Nach dem Parsen einer Nachricht/eines Folders in das Dict, **vor** dem `serialize`:
Header-Adressen + Anzeigenamen → `Vorname <key>` tauschen; Betreff/Body per Regex
nach Adressen durchsuchen und ersetzen; dabei die In-Memory-Rückwärts-Map befüllen.
- Eingriffspunkt: [`src/mail_mcp/imap/_parse.py`](../src/mail_mcp/imap/_parse.py)
  (`message_to_dict` / `folder_to_dict`) und
  [`src/mail_mcp/imap/read.py`](../src/mail_mcp/imap/read.py).

### Eingehend (LLM → Server)
- **Senden:** `to`/`cc`/`bcc`/`from-name` können Pseudonyme tragen. `<key>` → echte
  Adresse auflösen; **unbekannter Token → Fehler** (Containment). Eingriffspunkt:
  [`src/mail_mcp/smtp.py`](../src/mail_mcp/smtp.py).
- **Query-Kriterien:** `from`/`to`-Prädikate können Pseudonyme tragen → vor dem Bau
  der IMAP-SEARCH auflösen. Eingriffspunkt:
  [`src/mail_mcp/imap/_criteria.py`](../src/mail_mcp/imap/_criteria.py).
- **Write (Flags/Move):** arbeitet mit `id`/`folder`, keine Adressen → unberührt.

---

## 7. Per-Account-Schalter (config.jmd, out-of-reach)

- **Default AN** (Privacy by Default, Art. 25 Abs. 2), pro Account abschaltbar —
  ausschließlich in der out-of-reach `config.jmd`, **nicht** per Frontmatter
  (sonst könnte ein LLM-gesetztes Flag den Schutz abschwächen).
- Felder pro Account: `pseudonymize` (Default `true`), `pseudonymize-domain`
  (Default `false`, opt-in) und **`mask-content`** (Default `true`) — Letzteres
  steuert die Inhalts-Maskierung (Server/IP/Telefon/Host:Port; MTT-Regelset).
  Eingriffspunkt: [`src/mail_mcp/_config.py`](../src/mail_mcp/_config.py)
  (`Account`-Dataclass), durchgereicht über `ConnectionInfo` in
  [`server.py`](../src/mail_mcp/server.py).

---

## 8. Containment-Eigenschaft

Der LLM kann nur Tokens adressieren, die der Server kennt → er kann **keine echte
Adresse erfinden**. Neue Empfänger bringt der **Nutzer** ein (echte Adresse als
Eingabe). Hinweis: Eine vom Nutzer im Chat eingegebene neue Adresse gelangt damit in
den Kontext — das ist bei einer ausdrücklich nutzerinitiierten Aktion akzeptiert.

---

## 9. Grenzfälle / offene Implementierungsfragen

- Normalisierung: Gmail-Punkte / `+tags` zusammenführen oder belassen?
- Token-Länge vs. Kollisionsrisiko (~6–8 Zeichen).
- Namens-Heuristik & Internationalisierung (nicht-lateinisch, „Nachname, Vorname").
- Body-Regex: Adressen in zitierten Replies, `mailto:`-Links; **obfuskierte**
  Formen („alice at acme dot com") bleiben außerhalb des Scopes.
- Multipart/HTML: Tausch in **beiden** Body-Alternativen (Text + HTML) konsistent.
- Kosten des Rückwärts-Map-Rebuilds bei großen Ordnern.
- **Sende-Bestätigung:** Die Erfolgsmeldung von `send` darf den aufgelösten
  **echten** Empfänger nicht zurück in den Kontext spiegeln — Bestätigung mit dem
  **Pseudonym** ausgeben (vgl. aktuelle Antwort in `smtp.py`, die `to` echo't).
- Anhänge: Dateinamen können Namen enthalten — vorerst außerhalb des Scopes.

---

## 10. Außerhalb des Scopes (entschieden)

- **Reveal-Feature:** bewusst **keines**. Braucht der Nutzer die echte Adresse hinter
  `Alice <key>`, schlägt er die Mail in seinem normalen Mail-Client nach — Klartext
  berührt nie den LLM-Pfad.
- **`jmd-mcp-oauth2`:** unverändert.
- **Tiefe Body-PII / NER:** siehe §2.

---

## 11. Betroffene Dateien (Implementierungsüberblick)

| Datei | Rolle |
|---|---|
| `src/mail_mcp/_pseudonym.py` *(neu)* | Token-Erzeugung, Secret-Bootstrap, In-Memory-Rückwärts-Map, Namens-Extraktion |
| `src/mail_mcp/imap/_parse.py` | Ausgehender Tausch (Header + Body-Regex) |
| `src/mail_mcp/imap/read.py` | Einbindung im Read-Pfad |
| `src/mail_mcp/smtp.py` | Eingehende Auflösung (Empfänger, `from-name`), Bestätigung |
| `src/mail_mcp/imap/_criteria.py` | Eingehende Auflösung (Suchprädikate) |
| `src/mail_mcp/_config.py` | Per-Account-Schalter `pseudonymize`/`mask-content` (config.jmd, Default an) |

---

## 12. Adressbuch / Kontakt-Import

Ermöglicht das Adressieren von Personen, deren Mail man (noch) nicht gelesen hat —
z. B. weil das Konto serverseitig für den MCP-Server gesperrt ist —, ohne dass deren
echte Adresse je in den LLM-Kontext gelangt. Konzeptionell ein **Seed für dieselbe
In-Memory-Rückwärts-Map** wie die Lese-Pseudonyme — bei jedem Serverstart aus den
Import-Dateien neu aufgebaut, nicht aus einer persistierten Tabelle: gleicher
HMAC-Token ⇒ konsistente Identität, egal ob aus gelesener Mail oder Import.

### 12.1 Quelle (Auto-Discovery im Config-Verzeichnis)

- Jede **`*.vcf`** im **Config-Verzeichnis** (`~/.jmd-mcp-mail/`, Env-Override
  `JMD_MCP_MAIL_HOME`) wird automatisch importiert — **keine** explizite
  Konfiguration, keine CLI-Args/Env-Liste. `.vcf` reinlegen genügt.
- Die Dateien liegen out-of-reach (Nutzer-Verantwortung) und werden
  serverseitig gelesen; über die Tool-Grenze läuft nichts.

### 12.2 Format

- **vCard** (`.vcf`) via `vobject` (Apple/Google/Outlook/Thunderbird).
- **Outlook-PST** (`.pst`) via **`libpff` (Python-Binding `pypff`)** — für das
  *neue* Outlook, das Kontakte praktisch nur noch als PST exportiert. Der Parser
  läuft über die Kontakt-Ordner (`IPM.Contact`), nimmt den Namen aus den festen
  MAPI-Tags und erntet Adressen wertbasiert; Provenienz-Adressen (Creator/
  LastModifier des Exporteurs) werden übersprungen, damit sie nicht fälschlich
  einem Kontakt zugeordnet werden.
  - **Abhängigkeit bewusst optional:** `pypff` ist **keine** Projekt-Dependency.
    Der Server prüft zur Laufzeit nur ihr Vorhandensein; fehlt sie, werden `.pst`
    übersprungen (Status `skipped`, nie fatal) — `.vcf` funktioniert unabhängig.
    **Installation: `pip install libpff-python`** (NICHT `pypff` — das ist ein
    fremdes Astronomie-Paket; das Binding importiert sich aber *als* `pypff`).
- **CSV ist bewusst nicht unterstützt** — kein kanonisches Schema über Clients
  hinweg; exportiere vCard (ggf. einmal konvertieren).

### 12.3 Speicherung & Lebenszyklus

- Das **Adressbuch ist strikt in-memory**, kein `contacts.jmd`. Die nutzereigenen
  Exporte (`*.vcf`/`*.pst`) sind die einzigen PII-Eingabedateien.
- Startup scannt das Config-Verzeichnis nach `*.vcf`/`*.pst` → In-Memory.
  `reimport` scannt zur Laufzeit neu.
- **`contacts.md` — das Re-ID-Transkript (Modell A: Single-Session).** Parallel zum
  Import schreibt der Server eine menschenlesbare Tabelle `Token ↔ Name+Adresse` ins
  Config-Verzeichnis, damit der Nutzer ein im Chat gesehenes Pseudonym selbst
  zurückauflösen kann. Eigenschaften:
  - **Transkript der laufenden Session, keine Datenquelle.** `sync()` schreibt die
    Datei aus den In-Memory-Zeilen neu (Overwrite, **kein** Merge mit einer
    Altdatei). Sie spiegelt damit exakt den Live-Stand und behauptet nie ein
    Mapping, das der Server nicht (mehr) auflösen könnte.
  - **An Session-Grenzen gelöscht.** `purge()` entfernt die Datei beim **Startup**
    (bevor irgendetwas geschrieben wird) und best-effort beim **Shutdown**
    (`try/finally` um `mcp.run()` plus `SIGTERM`/`SIGINT`-Handler). Ein harter Kill
    (`SIGKILL`, Stromausfall) lässt sich nicht abfangen — der nächste Startup-Purge
    räumt dann auf, bevor ein Token geschrieben wird.
  - **Re-Identifikationsschlüssel — privat halten.** Die Datei enthält echte
    Adressen; sie wird nur vom Server geschrieben und von **keinem** Tool gelesen.

### 12.4 Label-Ableitung — Form `<Namensteil> <key>`

`<key>` (Einweg-HMAC, identisch zur Lese-Pseudonymisierung) ist **immer** Bestandteil
und trägt die Eindeutigkeit. Der Namensteil ist reine Lesbarkeits-/Wiedererkennungs-
Hilfe:

- Basis: **Vorname** (aus vCard `N`, wo Vor-/Nachname klar getrennt sind).
- Bei Vornamen-Kollision: + **kürzestes eindeutiges Nachnamen-Präfix** + „."
  (`Rebecca Sch.` vs. `Rebecca Spe.`).
- Mehrere Adressen pro Kontakt: **je Adresse ein diskreter Eintrag/Token**, im Label
  per vCard-`TYPE` unterschieden (`(geschäftlich)` / `(privat)`).
- Kein Nachname / Trennung nicht möglich → nur `Vorname <key>` bzw. reiner `<key>`.

Beispiele: `Rebecca <k7f2a9>`, `Rebecca Sch. <k7f2a9>`,
`Rebecca Sch. (geschäftlich) <k7f2a9>`.

### 12.5 Tool-Oberfläche

- Eigenes **`contacts`**-Tool: `# Contacts[]` listet die Einträge; `# Contacts {
  reimport: true }` scannt das Config-Verzeichnis neu und liefert zusätzlich einen
  **Per-Datei-Report** (`## files[]`: name/status/contacts) plus die Einträge.
- **Rückgabe-Invariante:** nur `(label, token)` (+ Datei-Report mit Name/Status/
  Anzahl); **niemals** Adressen oder Roh-Inhalt.

### 12.6 Auflösung beim Senden / Suchen

- `send` löst `to`/`cc`/`bcc` per **Label *oder* Token** → echte Adresse
  (serverseitig, nie im Kontext). Unbekannt → `unknown_pseudonym` (Containment).
- *Optional, noch nicht umgesetzt:* der Lese-Pfad könnte den Namensteil aus den
  Kontakten anreichern (`Rebecca Sch. <key>` statt `Rebecca <key>`) — gleicher
  `<key>`, gleiche Identität.

### 12.7 Betroffene Dateien

| Datei | Rolle |
|---|---|
| `src/mail_mcp/_contacts.py` *(neu)* | Auto-Discovery der `*.vcf`/`*.pst` im Config-Verzeichnis, vCard- + PST-Parser, Label-Ableitung, Seed der In-Memory-Map, Per-Datei-Report |
| `src/mail_mcp/_transcript.py` *(neu)* | `contacts.md`-Session-Transkript: Overwrite-`sync()` aus den In-Memory-Zeilen, `purge()` an den Session-Grenzen |
| `src/mail_mcp/_pseudonym.py` | gemeinsame Token-Erzeugung + Rückwärts-Map (von Kontakten mitgenutzt), Transkript-Zeilen |
| `src/mail_mcp/server.py` | `contacts`-Tool + Startup-Import; Startup/Shutdown-`purge()`; Label-Anreicherung im Read-Pfad |
| `src/mail_mcp/smtp.py` / `imap/_criteria.py` | Auflösung per Label (zusätzlich zum Token) |

---

## 13. Container-Scoping (absender-verankert)

Schließt Mail aus, deren *Inhalt* mit hoher Wahrscheinlichkeit sensibel ist, weil
sie aus einer **systematisch sensiblen Beziehung** stammt (Betriebsarzt, Anwalt,
Bank, Versicherung, HR …). Ergänzt die Pseudonymisierung (schützt *wer*) und das
Masking (schützt *was im Text*) um eine dritte Achse: *ob die Mail überhaupt
verarbeitet wird*.

### 13.1 Mechanismus — am Absender, nicht an der Empfängerliste

- Eine **Exclude-Menge** über **Adressen und Domänen**, serverseitig ausgewertet
  am **Absender** (`From`, ggf. `Reply-To`). Trifft sie zu → die Mail wird
  vollständig aus `read`/Query weggelassen (kein Body, keine Identität); der
  Zähler meldet „N gesamt, M in scope".
- **Bewusst NICHT an der Empfängerliste** (`To`/`Cc`): Die Liste ist genau die
  Dimension, die von der Empfänger-Disziplinlosigkeit der Versender verseucht ist.
  Sie als Drop-Kriterium zu nehmen, würde die Krankheit zum Filter machen und den
  Großteil legitimer Geschäftsmail verschwinden lassen — Ziel verfehlt. Der
  Absender dagegen ist das verlässliche Signal für die *Natur* einer Mail.

### 13.2 Haltung & Default

- **Default permissiv**: nur explizit sensible Absender/Domänen raus — Coverage
  hat Vorrang (das Tool soll die Flut bewältigen).
- **Optionaler strikter Modus**: Absender-*Whitelist* (nur `From` bekannter
  Domänen) für Hochsensibilitäts-Accounts — **nicht** Default, da er den externen
  Bulk killt.
- Quelle wie bei Kontakten: aus der MCP-Server-Config (CLI-Args / Env), außerhalb
  der LLM-Reichweite.
- **self-Adressen** konfigurierbar (Login + optionale Aliase), damit selbst
  gesendete Mail korrekt als „von mir" erkannt wird.

### 13.3 Bewusst verworfen / Restfall

- Empfängerlisten-basierter Drop (siehe 13.1).
- **Irreduzibler Rest**: Eine Mail, die einen Sensiblen nur *einschließt* (aber
  nicht von ihm stammt), wird verarbeitet (Adresse tokenisiert, Body nur
  regex-maskiert). Ebenso der *vermischte* Fall (in-scope-Absender schreibt
  beiläufig Sensibles). Beides bleibt der dokumentierten Risikoabwägung des
  Verantwortlichen (DSFA) — durch Scoping verengt, nicht geschlossen.

## 14. Send-seitige Empfänger-Hygiene (Roadmap)

Die aktive Mitigation der Empfänger-Disziplinlosigkeit gehört in den `send`-Pfad,
nicht ins Read-Scoping: eine **Warnung vor dem Senden** bei großen / gemischten /
externen Empfängerlisten (z. B. „Antwort an N Empfänger, davon X extern, Y
außerhalb deiner Domäne — wirklich an alle?"). Macht aus dem Problem ein Feature.
Designkonsens 2026-06-23; Detail-Spezifikation und Umsetzung offen.

---

*Stand 2026-06-23. Umgesetzt: Identitäts-Pseudonymisierung (f58215f),
Kontakt-Import (059ec0c), Content-Masking (8a1851a). Designkonsens, noch nicht
umgesetzt: Container-Scoping (§13), Send-Empfänger-Hygiene (§14),
Lese-Pfad-Anreicherung (§12.6). Umsetzung je nach ausdrücklichem Go.*
