# DSGVO-Konformität von Claude Cowork / Claude Code

**Ein praxisorientierter Leitfaden für Einzelpersonen und Organisationen**

Stand: 23. Juni 2026 · Sprache: de-DE

> ⚠️ **Kein Rechtsrat.** Dieses Dokument ist eine technisch-organisatorische
> Handreichung zur Vorbereitung einer datenschutzkonformen Nutzung. Es ersetzt
> keine Prüfung durch eine:n Datenschutzbeauftragte:n oder eine Rechtsberatung.
> Verträge, Zertifizierungsstatus und Rechtslage ändern sich — die mit ⏱
> markierten Angaben sind vor produktiver Nutzung zu verifizieren.

---

## 1. Worum es geht

Claude Cowork und Claude Code sind **agentische Werkzeuge** von Anthropic (USA).
Sie lesen lokale Dateien, führen Befehle aus, binden externe Werkzeuge (MCP-Server)
ein und senden dabei Inhalte an ein Sprachmodell, das — je nach Konfiguration — in
einem **Drittland (USA)** verarbeitet wird.

Daraus folgt für die DSGVO die Kernfrage: *Unter welchen Voraussetzungen dürfen
personenbezogene Daten an dieses Modell übermittelt werden?* Die gute Nachricht
vorweg: Das ist **kein per-se-Verbot**, sondern ein an Bedingungen geknüpftes,
grundsätzlich lösbares Übermittlungsszenario. Lokale Modelle sind dafür **nicht**
erforderlich.

---

## 2. Wer ist wofür verantwortlich? (Rollen)

| Rolle | Wer | Pflichten |
|---|---|---|
| **Verantwortlicher** (Art. 4 Nr. 7) | Du / deine Organisation | Rechtsgrundlage, AVV abschließen, Betroffene informieren, VVT, ggf. DSFA |
| **Auftragsverarbeiter** (Art. 28) | Anthropic | Weisungsgebundene Verarbeitung, TOMs, Unterauftragsverarbeiter, Meldepflichten |

**Merksatz:** Die meisten DSGVO-Pflichten treffen *dich als Verantwortliche:n*,
nicht den Anbieter. Anthropic stellt die Bausteine bereit (AVV, SCC, Konfiguration);
einsetzen und dokumentieren musst du sie selbst.

**Privatnutzung:** Eine rein private, familiäre Nutzung kann unter die
Haushaltsausnahme (Art. 2 Abs. 2 lit. c) fallen. Sobald du beruflich arbeitest
oder Daten Dritter (Kunden, Kommunikationspartner) verarbeitest, gilt die DSGVO
vollumfänglich.

---

## 3. Die vier Voraussetzungen im Überblick

Für eine konforme Nutzung müssen **alle vier** erfüllt sein:

1. **Rechtsgrundlage (Art. 6)** — z. B. Vertragserfüllung (lit. b) oder
   berechtigtes Interesse (lit. f, mit Abwägung). Bei besonderen Kategorien
   (Art. 9 — Gesundheit, etc.) zusätzlich ein Erlaubnistatbestand des Art. 9.
2. **Auftragsverarbeitungsvertrag / DPA (Art. 28)** — mit Anthropic.
3. **Drittland-Transfermechanismus (Kapitel V)** — DPF-Angemessenheitsbeschluss
   *und/oder* Standardvertragsklauseln (SCC).
4. **Datenschutzfreundliche Konfiguration** — Training-Opt-out, keine/kurze
   Datenspeicherung, Zugriffskontrolle.

Die folgenden Abschnitte arbeiten jede Voraussetzung ab.

---

## 4. Die entscheidende Weiche: Tarif / Bezugsweg

Ob ein AVV überhaupt zustande kommt, hängt am gewählten Produkt. **Das ist der
häufigste Stolperstein.**

| Bezugsweg | AVV (Art. 28) | Drittland-Absicherung | Training standardmäßig | EU-Residenz möglich |
|---|---|---|---|---|
| **Free / Pro / Max (Consumer)** | ❌ kein AVV | — | abhängig vom Opt-out | ❌ |
| **Team** ⏱ | i. d. R. kein AVV | — | — | ❌ |
| **API / Commercial Terms** | ✅ im Commercial-Terms-DPA enthalten | SCC (2021); DPF ⏱ | ❌ kein Training | ✅ via Amazon Bedrock |
| **Enterprise / Claude for Work** | ✅ | SCC + ggf. DPF ⏱ | ❌ | ✅ + Zero Data Retention |

**Konsequenz:**
- Für die Verarbeitung personenbezogener Daten Dritter sind die **Consumer-Tarife
  ungeeignet** (kein AVV).
- Der **API-/Commercial-Weg** ist auch für Freelancer und KMU erreichbar
  (Pay-per-Token, kein Enterprise-Vertrag nötig) und liefert AVV, SCC,
  „kein Training" und optional EU-Residenz.

> ⏱ **Zu prüfen:** Welcher konkrete Bezugsweg in deinem Claude-Cowork-/Code-Setup
> aktiv ist und ob für ihn ein AVV vorliegt. Bei Nutzung über die Anthropic-Konsole
> bzw. API ist der DPA Teil der Commercial Terms.

---

## 5. AVV / Data Processing Addendum abschließen

- **Anthropic:** Der DPA ist in die **Commercial Terms of Service** integriert —
  mit Zustimmung zu den Commercial Terms (Console/API) gilt der DPA, ohne
  separaten Signaturprozess. Er umfasst die Art.-28-Pflichten (Sicherheit,
  Unterauftragsverarbeiter, Datenpannenmeldung) und enthält die **EU-SCC 2021**.
- **Praxis:** AVV-Fassung herunterladen/abspeichern, Datum und Version
  dokumentieren, in die VVT-Unterlagen aufnehmen.

---

## 6. Drittlandtransfer absichern (Kapitel V)

Zwei Mechanismen — in der Praxis kombiniert:

- **EU-US Data Privacy Framework (DPF):** Angemessenheitsbeschluss seit Juli 2023.
  Gilt nur für **aktiv zertifizierte** Anbieter. ⏱ Status auf der offiziellen Liste
  prüfen: `https://www.dataprivacyframework.gov`.
- **Standardvertragsklauseln (SCC 2021):** Vertraglicher Rückfall, im
  Anthropic-DPA enthalten. **Empfehlung:** SCC als Rückfallebene halten, auch wenn
  man sich primär auf das DPF stützt — das DPF ist politisch angreifbar
  („Schrems III").

**Stärkster technischer Hebel — EU-Datenresidenz:**
Claude lässt sich über **Amazon Bedrock in der Region `eu-central-1` (Frankfurt)**
betreiben; dann findet die Inferenz **innerhalb der EU** statt und das
Drittland-Problem entschärft sich an der Wurzel. Ob das in deinem Cowork-/Code-Host
einstellbar ist, hängt vom **Host/Anbindung** ab (nicht von einem einzelnen
MCP-Server). ⏱ Verfügbarkeit/Region in deiner Konfiguration verifizieren.

---

## 7. Datenschutzfreundliche Konfiguration

- **Kein Training auf deinen Daten:** Bei API/Commercial standardmäßig der Fall;
  bei Consumer-Tarifen das Trainings-Opt-out aktiv setzen.
- **Datenspeicherung minimieren:** Chat-/Verlaufsspeicherung deaktivieren, wo
  möglich; **Zero Data Retention (ZDR)** beantragen (Enterprise/API), sodass
  Prompts/Completions nach dem Aufruf nicht gespeichert werden.
- **Zugriffskontrolle lokal:** Claude Code/Cowork greifen auf das Dateisystem zu.
  Arbeitsverzeichnisse so wählen, dass keine unnötigen personenbezogenen Daten im
  Zugriff liegen; Berechtigungs-/Permission-Einstellungen restriktiv halten.
- **MCP-Server prüfen:** Jeder eingebundene MCP-Server kann personenbezogene Daten
  in den Modellkontext ziehen (z. B. E-Mail-, CRM-, Datei-Konnektoren). Nur
  vertrauenswürdige, datensparsame Server einbinden.

---

## 8. Rechtsgrundlage und der heikle Punkt: Daten Dritter

Inhalte, mit denen Claude arbeitet (Quellcode, Dokumente, E-Mails, Tickets),
enthalten häufig personenbezogene Daten **Dritter** — Kunden, Kolleg:innen,
Kommunikationspartner —, die **nicht eingewilligt** haben und nicht damit rechnen,
dass ihre Daten an ein US-LLM gehen.

- Tragfähig ist hier meist nur **berechtigtes Interesse (Art. 6 Abs. 1 lit. f)**
  mit dokumentierter **Interessenabwägung**. Die Aufsichtsbehörden legen hier
  strenge Maßstäbe an (vgl. EDPB-Opinion 28/2024, ChatGPT-Taskforce).
- **Datenminimierung & Pseudonymisierung (Art. 5 Abs. 1 lit. c, Art. 25,
  Erwägungsgrund 28)** sind das, was die Abwägung *überhaupt erst tragfähig* macht
  und das übermittelte Risiko senkt. Faustregel: **Keine ungeschützten
  personenbezogenen Daten ohne AVV in das Tool geben** — und auch mit AVV nur das
  Nötige.
- **Besondere Kategorien (Art. 9):** Gesundheits-, Religions-, Gewerkschaftsdaten
  etc. nach Möglichkeit gar nicht oder nur pseudonymisiert übermitteln.

---

## 9. Datenschutz-Folgenabschätzung (DSFA, Art. 35)

- **Gelegentliche, risikoarme Textverarbeitung:** Eine vollständige DSFA ist oft
  nicht zwingend — eine **dokumentierte Risikoeinschätzung** wird dennoch empfohlen.
- **DSFA erforderlich**, wenn die Verarbeitung **voraussichtlich ein hohes Risiko**
  birgt, insbesondere bei systematischer/umfangreicher Verarbeitung sensibler Daten
  (z. B. ganze Kundenkorrespondenz, Personaldaten, Gesundheitsdaten).
- Die DSFA dokumentiert Zweck, Notwendigkeit, Risiken und **Abhilfemaßnahmen**
  (Pseudonymisierung, EU-Residenz, Zugriffsbeschränkung).

---

## 10. Weitere Pflichten der/des Verantwortlichen

- **Verzeichnis von Verarbeitungstätigkeiten (VVT, Art. 30):** Verarbeitung
  „KI-gestützte Bearbeitung von … mit Claude" aufnehmen — inkl. Anbieter, AVV,
  Transfermechanismus, Datenkategorien, Löschkonzept.
- **Betroffeneninformation (Art. 13/14):** Datenschutzerklärung um den Einsatz von
  KI und die US-Übermittlung ergänzen.
- **Betroffenenrechte (Art. 15–22):** Auskunft, Löschung etc. müssen erfüllbar
  bleiben — ZDR und lokale Datenminimierung helfen.
- **EU AI Act, Art. 4 (KI-Kompetenz):** seit Februar 2025 **größenunabhängig** —
  Mitarbeitende, die KI einsetzen, müssen hinreichend geschult sein. Auch
  Einzelunternehmer:innen sind adressiert.

---

## 11. Besonderheiten von Claude Code / Cowork (agentische Nutzung)

Anders als bei einem reinen Chat verarbeitet ein Agent **aktiv** Inhalte aus deiner
Umgebung. Das vergrößert die Angriffsfläche:

- **Was das Gerät verlässt:** Datei-/Code-Inhalte, Befehlsausgaben, von MCP-Servern
  geladene Daten — alles, was in den Modellkontext gerät, kann übermittelt werden.
- **Repository-Hygiene:** Keine Secrets/personenbezogenen Daten in Klartext in
  Arbeitsverzeichnissen; `.gitignore`/Ausschlüsse nutzen; sensible Ordner aus dem
  Agenten-Zugriff nehmen.
- **MCP-Konnektoren als Datenquellen:** Genau dieselbe Datenschutzlogik gilt für
  jeden eingebundenen Server (Mail, CRM, Cloud-Speicher). Datensparsamkeit und —
  wo möglich — **Pseudonymisierung am Konnektor-Rand** reduzieren das Exfiltrierbare
  unabhängig vom gewählten Tarif.
- **Logging/Telemetrie:** Prüfen, welche lokalen Logs/Telemetrie anfallen und ob
  diese personenbezogene Daten enthalten.

---

## 12. Restrisiken, die kein Vertrag vollständig löst

- **US CLOUD Act / FISA 702:** US-Behörden können Datenherausgabe verlangen — auch
  aus EU-Rechenzentren. Konflikt mit **Art. 48 DSGVO**. DPF/SCC mildern dies
  rechtlich, lösen es technisch nicht. **EU-Residenz** + Pseudonymisierung sind die
  wirksamsten Gegenmittel.
- **DPF-Fragilität:** Anfechtung möglich; bei Wegfall greifen die SCC. Deshalb beide
  parallel halten.

---

## 13. Umsetzungs-Checkliste

```
[ ] Bezugsweg klären: API/Commercial oder Enterprise wählen (NICHT Consumer für
    personenbezogene Daten Dritter)
[ ] AVV/DPA abschließen und dokumentieren (Version, Datum)
[ ] DPF-Zertifizierungsstatus des Anbieters auf dataprivacyframework.gov prüfen ⏱
[ ] SCC als Rückfallebene sicherstellen (im DPA enthalten)
[ ] EU-Datenresidenz prüfen/aktivieren (z. B. Bedrock eu-central-1) ⏱
[ ] Training-Opt-out / Zero Data Retention konfigurieren
[ ] Rechtsgrundlage je Verarbeitung festlegen (Art. 6; ggf. Art. 9)
[ ] Interessenabwägung für Daten Dritter dokumentieren
[ ] Datenminimierung/Pseudonymisierung als Standard etablieren
[ ] Risikoeinschätzung; bei hohem Risiko DSFA (Art. 35)
[ ] VVT-Eintrag (Art. 30) anlegen
[ ] Datenschutzerklärung ergänzen (Art. 13/14)
[ ] Arbeitsverzeichnisse/MCP-Konnektoren auf Datensparsamkeit prüfen
[ ] KI-Kompetenz/Schulung sicherstellen (EU AI Act Art. 4)
[ ] Lokale Logs/Telemetrie auf personenbezogene Daten prüfen
```

---

## 14. Quellen

- EU-US Data Privacy Framework – Status 2026: <https://next-levels.de/wiki/eu-us-data-privacy-framework>
- Datenübermittlung in Drittstaaten / DPF – IHK München: <https://www.ihk-muenchen.de/ratgeber/recht/datenschutz/datenuebermittlung-in-drittstaaten/>
- KI + Datenschutz 2026 (ChatGPT, Claude & Co.) – easyRechtssicher: <https://easyrechtssicher.de/blog/ki-und-datenschutz>
- Anthropic API: AVV, EU-Datenresidenz & Compliance – compound.law: <https://compound.law/de-DE/tools/anthropic-api/>
- Offizielle DPF-Teilnehmerliste: <https://www.dataprivacyframework.gov>
- EDPB – Report of the ChatGPT Taskforce: <https://www.edpb.europa.eu/our-work-tools/our-documents/other/report-work-undertaken-chatgpt-taskforce_en>
- EDPB Opinion 28/2024 zu KI-Modellen (Zusammenfassung) – Debevoise Data Blog: <https://www.debevoisedatablog.com/2025/04/14/gdpr-considerations-when-developing-and-deploying-ai-models-the-edpbs-opinion-on-compliance/>
- Steht das DPF vor dem Aus? – KPMG Klardenker: <https://klardenker.kpmg.de/financialservices-hub/data-privacy-framework-datenaustausch-mit-den-usa-erneut-auf-dem-pruefstand/>

---

*Erstellt zur internen Vorbereitung der Datenschutz-Compliance. Vor produktivem
Einsatz durch Datenschutzbeauftragte:n / Rechtsberatung prüfen lassen.*
