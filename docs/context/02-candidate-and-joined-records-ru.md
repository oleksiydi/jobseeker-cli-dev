# Данные кандидата и реальные join-примеры (list + details)

## 1. Назначение
Файл содержит исходные данные для отладки нормализации/ранжирования и проверки join по `referenznummer`.
Источник join-данных: локальная MongoDB, коллекции `stellenangebote` и `jobdetails`.

## 2. Профиль кандидата (default)
Путь: `conf/profiles/default.profile.json`
```json
{
    "Berufsprofil": {
        "Beschreibung": "Als versierter IT-Experte mit umfangreicher Erfahrung in verschiedenen IT-Bereichen, einschließlich Big Data, Softwareentwicklung, Testverfahren und DevOps, habe ich maßgeblich zur Effizienzsteigerung und Optimierung technischer Lösungen beigetragen. Meine Expertise umfasst die Entwicklung, Skalierung und Automatisierung von Prozessen sowie die Durchführung von umfassenden Testverfahren. Ich habe in zahlreichen Projekten gearbeitet, in denen ich mein technisches Know-how und meine Problemlösungsfähigkeiten unter Beweis gestellt habe.",
        "AktuellerStandort": "Derzeit lebe ich in Deutschland.",
        "Erfahrung": "Obwohl ich Erfahrung im Programmieren habe, war ich überwiegend in sehr vielfältigen Tätigkeiten aktiv."
    },
    "Berufserfahrung": [
        {
            "Position": "Big Data Ingenieur",
            "Firma": "Comcast",
            "Jahr": "2022",
            "Art": "Freelance",
            "Aufgaben": [
                "Entwicklung und Skalierung von Lösungen mit Apache Spark und Databricks über AWS Lambda.",
                "CI/CD-Management mit Concourse CI zur Steigerung der Prozesseffizienz und Produktivität.",
                "Ausführung von Scala-Programmen auf Databricks-Clustern zur Verarbeitung großer Datenmengen.",
                "Erstellung von CloudWatch-Dashboards zur Überwachung der Leistungsmetriken von Lambda-Funktionen."
            ]
        },
        {
            "Position": "Freelancer-Teammitglied",
            "Firma": "Freelancer-Team",
            "Jahr": "2013 - 2021",
            "Art": "Freelance",
            "Aufgaben": [
                "Anforderungsmanagement, Softwaretests und Softwareentwicklung.",
                "Implementierung und Support von Systemen für groß angelegte Videoüberwachung und ein effizientes Ticketsystem.",
                "Kommunikation mit Kundenvertretern auf allen Ebenen, von Kassierern bis hin zu Managern des oberen Managements.",
                "Erstellung technischer Dokumentationen, Benutzerhandbücher und Dokumentationen für REST-APIs.",
                "Integration von Ticketverkaufssystemen mit externen Systemen und API-Analysen."
            ]
        },
        {
            "Position": "Ingenieur",
            "Firma": "Staatliche Akademie für Bauwesen und Architektur Odessa",
            "Jahr": "2012 - 2013",
            "Aufgaben": [
                "Hardware- und Software-Support, Entwicklung von Tools zur Modellierung physischer Prozesse.",
                "Organisation und Support des Informatiklabors der Physikabteilung."
            ]
        },
        {
            "Position": "Software-Ingenieur",
            "Firma": "Luxoft, Ukraine",
            "Jahr": "2011",
            "Aufgaben": [
                "Erstellung und Durchführung von Testfällen, Einrichtung und Support von Continuous-Integration-Systemen.",
                "Einführung eines automatisierten Testsystems mit Hyper-V- und TeamCity-Servern.",
                "Branchen-Merges für ein CAD-System zur Optimierung von Teilen für Metallbearbeitungsmaschinen.",
                "Code-Integration und Konfliktlösung."
            ]
        },
        {
            "Position": "Privatunterrichtlehrer, DBA-Softwareentwickler",
            "Jahr": "2009 - 2011",
            "Art": "Freelance",
            "Aufgaben": [
                "Privatunterricht in IT-Grundlagen.",
                "Erstellung kundenspezifischer kleiner Datenbanken in Microsoft Access."
            ]
        },
        {
            "Position": "Softwaretest-Ingenieur",
            "Firma": "UM-Service (RingCentral)",
            "Jahr": "2008 - 2009",
            "Aufgaben": [
                "Erstellung von Software-Anforderungsspezifikationen (SRS), Festlegung von Teststrategien und Erstellung von Testfällen.",
                "Ausführung von Testfällen und Diagnose von Problemen für SIP-Telefonie und MacOS-Anwendungen."
            ]
        },
        {
            "Position": "Softwaretest-Ingenieur",
            "Firma": "The Product Engine",
            "Jahr": "2007 - 2008",
            "Aufgaben": [
                "Erstellung und Durchführung von Testfällen, Analyse der Ergebnisse.",
                "Erstellung von Testdaten, selbständige Fehlerbehebung.",
                "Kodierung und Dekodierung von Videos.",
                "Arbeit mit DVR-Systemen.",
                "Durchführung von Blackbox- und Whitebox-Tests (C++)."
            ]
        },
        {
            "Position": "Softwareentwickler",
            "Firma": "Handelsunternehmen 'Ruta'",
            "Jahr": "2005 - 2006",
            "Aufgaben": [
                "Entwicklung und Implementierung eines Workflow-Management-Systems in Lotus Domino.",
                "Schulung der Mitarbeiter im von mir erstellten Dokumentenverwaltungssystem und in den Datenbanken und Automatisierungssystemen."
            ]
        },
        {
            "Position": "Freiberuflicher Dozent",
            "Firma": "Computer Akademie 'Step'",
            "Jahr": "2005",
            "Aufgaben": [
                "Unterricht in Grundlagen der Programmierung in C++ und PC-Nutzung für Kinder und Erwachsene."
            ]
        },
        {
            "Position": "Berater für Lagerlogistik",
            "Firma": "Avers Logistic",
            "Jahr": "2005",
            "Aufgaben": [
                "Empfehlung und Berechnung der erforderlichen Lagerausstattung.",
                "Empfehlungen zur Verbesserung des Lagerbetriebs."
            ]
        },
        {
            "Position": "Dozent",
            "Firma": "Odessa Nationale Polytechnische Universität",
            "Jahr": "2005",
            "Lehrstuhl": "Computersysteme und Netzwerke",
            "Aufgaben": [
                "Durchführung von Kursen für Informatikstudenten zu den Grundlagen von Datenbanken.",
                "Ein Teil der Kurse wurde auf Deutsch gehalten."
            ]
        },
        {
            "Position": "Freiberuflicher Dozent",
            "Firma": "Volkshochschule Regensburg",
            "Jahr": "2003",
            "Aufgaben": [
                "Durchführung von EDV-Trainings mit den Schwerpunkten EDV-Grundlagen und Office-Anwendungen."
            ]
        }
    ],
    "Ausbildung": [
        {
            "Titel": "Spezialist für Radioelektronik",
            "Institution": "Odessa Nationale Polytechnische Universität",
            "Jahr": "1996 – 2001"
        },
        {
            "Titel": "Gaststudium und DSH-Vorbereitungskurs",
            "Institution": "Universität Regensburg",
            "Jahr": "2001 – 2003"
        }
    ],
    "Sprachkenntnisse": {
        "Deutsch": "fließend",
        "Englisch": "technische Englischkenntnisse",
        "Russisch": "Muttersprache",
        "Ukrainisch": "Muttersprache"
    },
    "Interessen": [
        "Lesen",
        "Selbstbildung"
    ],
    "TechnischeKenntnisse": {
        "Programmiersprachen": [
            "Java",
            "Scala",
            "Bash",
            "SQL",
            "Spark SQL",
            "MongoDB Query Language",
            "@Formula",
            "Lotus Script",
            "C++"
        ],
        "Netzwerkprotokolle": [
            "TCP/IP",
            "HTTP",
            "SIP",
            "RTP"
        ],
        "VirtualisierungUndCloud": [
            "Docker",
            "Kubernetes (K8s)",
            "Hyper-V",
            "AWS",
            "AWS Lambda",
            "Microsoft Azure",
            "Digital Ocean"
        ],
        "EntwicklungsUndTestwerkzeuge": [
            "Visual Studio Code",
            "IntelliJ IDEA",
            "Maven",
            "Jenkins",
            "Concourse CI",
            "Jira",
            "Bugzilla",
            "TestLink",
            "Wireshark",
            "HTTP Analyzer",
            "GraphEdit",
            "StreamEye",
            "Git",
            "TeamCity",
            "Lotus Domino Server",
            "MS Access"
        ],
        "Methodologien": [
            "Agile",
            "Scrum",
            "Wasserfall (Waterfall)"
        ]
    },
    "zusätzliche_informationen": {
        "flüchtlingsstatus": "Ich bin ein ausländischer Flüchtling mit dem Recht, in Deutschland zu arbeiten.",
        "körperliche_verfassung": "Mein körperlicher Zustand erlaubt es mir nicht, schwere körperliche Arbeit zu verrichten.",
        "sprachkenntnisse": "Meine Sprachkenntnisse sind gut, aber nicht perfekt.",
        "lernbereitschaft": "Ich bin bereit und stehe dem Lernen und Selbstlernen positiv gegenüber.",
        "leidenschaft_für_lehre": "Ich liebe es sehr, jeden zu unterrichten, einschließlich mich selbst."
    }
}
```

## 3. Резюме кандидата (raw txt)
Путь: `conf/profiles/test.profile.txt`
```text
Oleksii Didyk (Alexej Didyk)
Deutschland (Bayern, Regensburg) | Arbeitserlaubnis in Deutschland vorhanden
Kontakt
E-Mail: (bitte eintragen)
Telefon: (bitte eintragen)
LinkedIn/GitHub: (optional)
Sprachen
Deutsch: gut bis fließend (nicht
perfekt)
Englisch: technisches Englisch
Russisch: Muttersprache
Ukrainisch: Muttersprache
Schwerpunkte
l DevOps, CI/CD, Containerisierung
l Troubleshooting, Incident
Response, Observability
l Big Data Pipelines
(Spark/Databricks, Streaming)
l Video-Systeme (RTSP/FFmpeg,
Transcoding) & VoIP (SIP/RTP)
l Integration, Rollout, Support &
Requirements Engineering
Technologien
Programmiersprachen: Java, Scala,
Bash, SQL, C/C++, R (PoC), Node.js
(Projektkontext), Spark SQL,
MongoDB Query Language, Lotus
Script, @Formula
Cloud/Virtualisierung: Docker,
Kubernetes, Helm, AWS (Lambda,
CloudWatch), Databricks, Hyper-V,
Microsoft Azure, Digital Ocean
Datenbanken: MongoDB,
PostgreSQL, MS Access, Lotus
Domino
Tools: Git, Jenkins, Concourse CI,
TeamCity, Jira, Bugzilla, TestLink,
Wireshark, IntelliJ IDEA, Visual Studio
Code, Maven
Methoden: Agile/Scrum, Wasserfall
Interessen
Lesen, Selbstbildung
Profil
Integration-/Troubleshooting-Ingenieur mit starkem DevOps-Background und
Erfahrung in QA/Entwicklung in komplexen Systemen (Video, VoIP, Big Data,
Industrial CAD/CAM). Erfahren in der Entwicklung, Skalierung und
Automatisierung von Prozessen sowie in systematischer Fehleranalyse mit
Logs/Metriken/Protokoll-Traces.
Berufserfahrung
Comcast - Big Data Engineer (Freelance/Contract) | 2022
l Entwicklung und Skalierung von Lösungen mit Apache Spark und
Databricks (AWS Lambda).
l Ausführung von Scala-Programmen auf Databricks-Clustern zur
Verarbeitung großer Datenmengen und Event-Streams.
l CI/CD-Management mit Concourse CI.
l Erstellung von CloudWatch-Dashboards zur Überwachung der
Leistungsmetriken von Lambda-Funktionen.
Freelancer-Team - Analyst / QA / DevOps / Support (Freelance) | 2013 -
2021
l Anforderungsmanagement, Softwaretests und Softwareentwicklung
(rollenflexibel je nach Projekt).
l Implementierung und Support von Systemen für groß angelegte
Videoüberwachung sowie eines Ticketsystems.
l Kommunikation mit Kundenvertretern auf allen Ebenen (von
Endanwendern bis Top-Management).
l Erstellung technischer Dokumentation, Benutzerhandbücher und
Dokumentation für REST-APIs.
l Integration von Ticketverkaufssystemen mit externen Systemen und
API-Analysen.
Weitere Stationen (Auswahl)
l Staatliche Akademie für Bauwesen und Architektur Odessa - Ingenieur
(2012 - 2013): Hardware-/Software-Support, Tools zur Modellierung
physischer Prozesse, Support Informatiklabor.
l Luxoft (Ukraine) - Software-/Test Engineer (2011): Testfälle, CI (TeamCity),
Hyper-V Testumgebungen, Code-Integration & Konfliktlösung,
CAD-System-Kontext.
l Freelance - Privatunterrichtlehrer & DBA-Softwareentwickler (2009 - 2011):
IT-Grundlagen, kleine Datenbanken in Microsoft Access.
l UM-Service (RingCentral) - Softwaretest-Ingenieur (2008 - 2009): SRS,
Teststrategie/Testfälle, Diagnose von SIP-Telefonie und
macOS-Anwendungen.
l The Product Engine - Softwaretest-Ingenieur (2007 - 2008): Testfälle,
Testdaten, Video-Kodierung/-Dekodierung, DVR-Systeme, Blackbox- und
Whitebox-Tests (C++).
l Handelsunternehmen 'Ruta' - Softwareentwickler (2005 - 2006):
Workflow-Management-System in Lotus Domino; Schulung der
Mitarbeitenden.
l Lehre/Training (2003, 2005): Dozent (Odessa Nationale Polytechnische
Universität - Datenbanken, teils auf Deutsch), freiberuflicher Dozent (Computer
Akademie 'Step' - C++/PC), freiberuflicher Dozent (VHS Regensburg -
EDV/Office).
Ausbildung
Odessa Nationale Polytechnische Universität - Spezialist für
Radioelektronik | 1996 - 2001
Universität Regensburg - Gaststudium & DSH-Vorbereitungskurs | 2001
```

## 4. Статистика выгрузки join-примеров
```json
{
  "generated_at": "2026-02-25T09:03:28.368448Z",
  "db_name": "jobcenter",
  "stellenangebote_count": 346,
  "jobdetails_count": 346,
  "examples_selected": 4,
  "selection_rule": "join by referenznummer; top by serialized jobdetails size"
}
```

## 5. Полные записи большого объема из двух коллекций с join по referenznummer
### 5.1 referenznummer = `15707-1486986-2-S`
```json
{
  "referenznummer": "15707-1486986-2-S",
  "size": 7581,
  "stellenangebote": {
    "referenznummer": "15707-1486986-2-S",
    "aktuelleVeroeffentlichungsdatum": "2026-01-15",
    "arbeitgeber": "Daimler Truck AG",
    "arbeitsort": {
      "plz": "94447",
      "ort": "Plattling",
      "strasse": "null",
      "region": "Bayern",
      "land": "Deutschland",
      "koordinaten": {
        "lat": 48.7858592,
        "lon": 12.8949361
      },
      "entfernung": "0"
    },
    "beruf": "Karosserie- und Fahrzeugbaumechaniker/in - Karosserieinstandhaltungstechnik",
    "created_at": "2026-02-23 01:02:16.424000",
    "eintrittsdatum": "2026-07-31",
    "is_active": true,
    "kundennummerHash": "ljXYoyZ9N9gd4nU54yvlp3E1PSmyo_hWN2jpOzW84tg=",
    "meta_hash": "f4bbb698677df92e0f8d87c5c8dc85e5f641fc970d53ea7a1d5e4bec9cb6235f",
    "modifikationsTimestamp": "2026-02-09T15:31:35.669",
    "modifikationsTimestamp_dt": "2026-02-09 14:31:35.669000",
    "refnr": "15707-1486986-2-S",
    "titel": "Ausbildung zum Karosserie- und Fahrzeugbaumechaniker, Fachrichtung Karosserieinstandhaltung...",
    "updated_at": "2026-02-23 01:02:16.424000"
  },
  "jobdetails": {
    "stellenangebotsart": "AUSBILDUNG",
    "ausbildungsart": "AUSBILDUNG",
    "stellenangebotsTitel": "Ausbildung zum Karosserie- und Fahrzeugbaumechaniker, Fachrichtung Karosserieinstandhaltung...",
    "quereinstiegGeeignet": false,
    "stellenangebotsBeschreibung": "Ausbildung zum Karosserie- und Fahrzeugbaumechaniker, Fachrichtung Karosserieinstandhaltung (w/m/d), Daimler Buses GmbH, Service Center in ​Plattling​, Ausbildungsbeginn 01.08.2026\n\nStellennummer: 5025\nEinsatzort: Plattling\nGesellschaft: Daimler Buses GmbH\nTätigkeitsbereich: Marketing/Vertrieb/Kommunikation\nArt der Anstellung: Vollzeit (teilzeitgeeignet)\n\n\nAufgaben\nBegeisterst Du dich für Busse im Premiumbereich? Willkommen in deiner beruflichen Zukunft. Mit der Ausbildung zum Karosserie- und Fahrzeugbaumechaniker (w/m/d) für Karosserieinstandhaltung startest Du bei uns voll durch. Ab dem ersten Tag lernst Du bei uns alles rund um die Diagnose, Wartung und Einstellung von Nutzfahrzeugen und mechatronischen Systemen. Du sorgst dafür, dass die Busse unserer Kunden wieder schnell dorthin gelangen, wo sie hingehören – auf die Straße! Mit deinem Faible für neue Fahrzeugtechnologien und alternative Antriebe bist Du bei uns genau richtig!\nWenn Du dir noch nicht sicher bist, ob WIR zu Dir passen, dann besteht jederzeit die Möglichkeit ein Schnupperpraktikum durchzuführen.\n\nWir sind ein Unternehmen der Daimler Truck AG und verantwortlich für die Wartung und Reparaturen an Reise- und Linienbussen der Marken Mercedes-Benz und Setra. In unserem Team sind aktuell ca. ​ 38 Mitarbeiter*innen besch ä ftigt. Wir pflegen eine sehr famili ä re Atmosph ä re und bieten geregelte Arbeitszeiten mit freiem Wochenende. Wenn Du gerne in einem Team arbeitest und jeden Tag abwechslungsreiche T ä tigkeiten durchf ü hren willst, dann starte bei uns in Deine berufliche Zukunft.\nWerde Teil unseres globalen Teams: Du machst den Unterschied - YOUMAKEUS Was dich erwartet\n- Beurteilung von Fahrzeug- und Karosserieschäden\n- Durchführung notwendiger Reparaturen und Montagearbeiten wie das Ausbeulen von Karosserieblechen\n- Umgang mit Werkzeugen und Maschinen zur Metallbearbeitung\n- Be- und Verarbeitung von Halbzeugen und Bauteilen\n- Mitwirken bei Planung, Steuerung, Kontrolle und Bewertung von Arbeitsabläufen\n- Demontage und Montage von Bauteilen, Baugruppen und Systemen\n- Herstellung von Oberflächen und Reparaturlackierungen\n- Einblicke in Elektrotechnik, Elektronik, Hydraulik und Pneumatik\n\n\nWas wir dir bieten\n\n- Nach der Ausbildung besteht die Möglichkeit der Übernahme in ein unbefristetes Arbeitsverhältnis mit Beschäftigungssicherung bis 2033\n- 30 Tage Jahresurlaub bei einer 36 Stunden Woche und 5 Arbeitstagen\n- Attraktive und sichere Altersvorsorge (Arbeitgeberfinanziert)\n- Regelmäßige Schulungen und Weiterentwicklungsangebote\n- Dauerhaftes Schulungsangebot über LinkedIn Learning\n- Möglichkeit einer Hospitation bei einem unserer weiteren Standorte\n- Teilnahme an Azubiveranstaltungen\n- Umfassende Mobilitätsangebote – zum Beispiel Mitarbeiterleasing von PKW oder E-Bike Leasing\n- Betriebsinterne Krankenkasse\n- Bezahlte Freistellung für wichtige familiäre Ereignisse\n- Tariflich (Tarifgemeinschaft des bayerischen Kraftfahrzeuggewerbes) abgesichertes Urlaubs- und Weihnachtsgeld\n- Bereitstellung von personalisierter Arbeitskleidung (inkl. Wäscheservice)\n\n\nQualifikationen\n\nDas bringst du mit\n\n- Einen erfolgreichen Schulabschluss\n- Solides Grundwissen in Mathematik und Physik\n- Handwerkliches Geschick, technisches Verständnis und großes Interesse an Fahrzeugen\n- Verantwortungsbewusstsein, Sorgfalt und Teamfähigkeit\n\nWir leben bunt und vielfältig. Deswegen suchen wir dich als Mensch mit all deiner Individualität.  #MAKEYOURMOVE und bewirb Dich jetzt - wir freuen uns auf Dich! Zusätzliche Informationen:\nZusätzliche Informationen:\n\n\n- Anlagen für Online-Bewerbung: ein Anschreiben, deinen Lebenslauf und die zwei letzten Zeugnisse\n- Interessierst du dich für unterschiedliche Ausbildungsberufe, teile uns deine Priorisierung im Anschreiben mit\n\n\nDu fühlst dich von unserem Angebot angesprochen und möchtest ein Teil des Teams werden? Dann bewirb dich direkt online mit deinen Bewerbungsunterlagen! Wir freuen uns insbesondere über Bewerbungen schwerbehinderter und ihnen gleichgestellter behinderter Menschen. Unter franz-peter.junginger@daimlertruck.com kannst du dich zudem an die Schwerbehindertenvertretung des Standorts wenden, der dich gerne im Bewerbungsprozess unterstützt.\n\nSolltest du bei deiner Online-Bewerbung Unterstützung benötigen, wir helfen dir gerne! Bei weiteren Fragen, wende dich an ausbildung-servicecenter@daimlertruck.com.\n\nÜbrigens: Wenn Du bereits in unserer Ausschreibung mit Du angesprochen wirst, nicht wundern – einfach zurückduzen. Im Service Center Neu-Ulm leben wir eine „Du-Kultur“. Falls Du Dich mit einem „Sie“ wohler fühlst – kleiner Hinweis genügt.\n\nUnsere Stärken: Hochmotivierte und begeisterte Mitarbeiter*innen mit großem Zusammengehörigkeitsgefühl und dem Willen, etwas zu bewegen. Denn bei Daimler Buses machst du den Unterschied. YOU MAKE US.\n\n#MAKEYOURMOVE und bewirb Dich jetzt – wir freuen uns auf Dich!\n\n\nUnsere Unternehmenskultur\nBei Daimler Truck fördern wir Vielfalt und stehen für eine inklusive Unternehmenskultur. Wir schätzen die individuellen Stärken unserer Mitarbeiter:innen, denn diese führen zur besten Teamleistung und somit zum Erfolg unseres Unternehmens. Inklusion und Chancengleichheit sind uns wichtig – ganz gleich woher Du kommst und wer Du bist. Wir freuen uns auf Bewerbungen von Personen aller Kulturen und Geschlechter, Eltern, Menschen mit Behinderungen und Menschen aus der LGBTIQ+ Community.\n\nBenefits\n-Kantine\n-Betriebsarzt\n-Mitarbeiter Events\n-Mitarbeiterrabatte möglich\n-Mitarbeiterhandy möglich\n-Gesundheitsmaßnahmen\n-Homeoffice möglich\n-Internetnutzung\n-Parkplatz\n-Gute Anbindung",
    "geforderterBildungsabschluss": "HAUPTSCHULABSCHLUSS",
    "arbeitszeitHeimarbeitTelearbeit": false,
    "arbeitszeitSchichtNachtWochenende": false,
    "arbeitszeitTeilzeitAbend": false,
    "arbeitszeitTeilzeitNachmittag": true,
    "arbeitszeitTeilzeitVormittag": true,
    "arbeitszeitTeilzeitFlexibel": false,
    "arbeitszeitVollzeit": true,
    "eintrittszeitraum": {
      "von": "2026-07-31"
    },
    "verguetungsangabe": "KEINE_ANGABEN",
    "vertragsdauer": "KEINE_ANGABE",
    "istGeringfuegigeBeschaeftigung": false,
    "istBehinderungGefordert": false,
    "stellenlokationen": [
      {
        "adresse": {
          "plz": "94447",
          "ort": "Plattling",
          "region": "BAYERN",
          "land": "DEUTSCHLAND"
        },
        "breite": 48.7858592,
        "laenge": 12.8949361
      }
    ],
    "chiffrenummer": "406704",
    "istPrivateArbeitsvermittlung": false,
    "istArbeitnehmerUeberlassung": false,
    "veroeffentlichungszeitraum": {
      "von": "2026-01-15"
    },
    "datumErsteVeroeffentlichung": "2026-01-15",
    "aenderungsdatum": "2026-02-09T15:31:35.669",
    "istBetreut": true,
    "hauptberuf": "Karosserie- und Fahrzeugbaumechaniker/in - Karosserieinstandhaltungstechnik",
    "firma": "Daimler Truck AG",
    "arbeitgeberKundennummerHash": "ljXYoyZ9N9gd4nU54yvlp3E1PSmyo_hWN2jpOzW84tg=",
    "allianzpartnerUrl": "www.daimler-truck.com",
    "allianzpartnerName": "Daimler Truck AG",
    "referenznummer": "15707-1486986-2-S",
    "details_hash": "a366e9f20c2e76c40d4553d083c7ff28f6d4c060cb07179cb0d58179d0cc6472",
    "last_seen_remote_updated_at": "2026-02-09T15:31:35.669",
    "last_seen_remote_updated_at_dt": "2026-02-09 14:31:35.669000",
    "aenderungsdatum_dt": "2026-02-09 14:31:35.669000",
    "last_checked_at": "2026-02-23 01:04:37.290000",
    "created_at": "2026-02-23 01:04:37.290000",
    "updated_at": "2026-02-23 01:04:37.290000"
  }
}
```

### 5.2 referenznummer = `15707-1486969-2-S`
```json
{
  "referenznummer": "15707-1486969-2-S",
  "size": 7465,
  "stellenangebote": {
    "referenznummer": "15707-1486969-2-S",
    "aktuelleVeroeffentlichungsdatum": "2026-01-15",
    "arbeitgeber": "Daimler Truck AG",
    "arbeitsort": {
      "plz": "94447",
      "ort": "Plattling",
      "strasse": "null",
      "region": "Bayern",
      "land": "Deutschland",
      "koordinaten": {
        "lat": 48.7858592,
        "lon": 12.8949361
      },
      "entfernung": "0"
    },
    "beruf": "Kraftfahrzeugmechatroniker/in - Nutzfahrzeugtechnik",
    "created_at": "2026-02-23 01:02:16.424000",
    "eintrittsdatum": "2026-07-31",
    "is_active": true,
    "kundennummerHash": "ljXYoyZ9N9gd4nU54yvlp3E1PSmyo_hWN2jpOzW84tg=",
    "meta_hash": "0ad94791ba419f1c99a3340dee38e89130c432e588d3573a150d0d97b5dbafd9",
    "modifikationsTimestamp": "2026-02-09T15:31:36.24",
    "modifikationsTimestamp_dt": "2026-02-09 14:31:36.240000",
    "refnr": "15707-1486969-2-S",
    "titel": "Ausbildung zum Kfz-Mechatroniker System- und Hochvolttechnik (w/m/d), Daimler Buses GmbH,...",
    "updated_at": "2026-02-23 01:02:16.424000"
  },
  "jobdetails": {
    "stellenangebotsart": "AUSBILDUNG",
    "ausbildungsart": "AUSBILDUNG",
    "stellenangebotsTitel": "Ausbildung zum Kfz-Mechatroniker System- und Hochvolttechnik (w/m/d), Daimler Buses GmbH,...",
    "quereinstiegGeeignet": false,
    "stellenangebotsBeschreibung": "Ausbildung zum Kfz-Mechatroniker System- und Hochvolttechnik (w/m/d), Daimler Buses GmbH, Service Center in ​Plattling​, Ausbildungsbeginn 01.08.2026\n\nStellennummer: 5026\nEinsatzort: Plattling\nGesellschaft: Daimler Buses GmbH\nTätigkeitsbereich: Marketing/Vertrieb/Kommunikation\nArt der Anstellung: Vollzeit (teilzeitgeeignet)\n\n\nAufgaben\n\nDu bist begeistert von der Mobilität der Zukunft und willst hier aktiv mitgestalten? Als Kraftfahrzeugmechatroniker (w/m/d) im Schwerpunkt System- und Hochvolttechnik bist Du in der Welt der alternativen Antriebstechnologien zu Hause. Ab dem ersten Tag lernst Du bei uns in der Servicewerkstatt, die modernen Busse zu warten und zu reparieren und lernst Themen wie beispielsweise modernste Elektro- und Hybridantriebe, Batterietechnologien, neue Bordnetze und Ladesysteme kennen.\n\nWenn Du dir noch nicht sicher bist, ob WIR zu Dir passen, dann besteht jederzeit die Möglichkeit ein Schnupperpraktikum durchzuführen.\n\nWir sind ein Unternehmen der Daimler Truck AG und verantwortlich für die Wartung und Reparaturen an Reise- und Linienbussen der Marken Mercedes-Benz und Setra. In unserem Team sind aktuell ca. ​ 38 ​ Mitarbeiter*innen besch ä ftigt. Wir pflegen eine sehr famili ä re Atmosph ä re und bieten geregelte Arbeitszeiten mit freiem Wochenende. Wenn Du gerne in einem Team arbeitest und jeden Tag abwechslungsreiche T ä tigkeiten durchf ü hren willst, dann starte bei uns in Deine berufliche Zukunft.\n\nWerde Teil unseres globalen Teams:\nDu machst den Unterschied - YOUMAKEUS Was dich erwartet\n- Arbeiten an etablierten und alternativen Antriebstechnologien wie Hochvolt sowie aktueller Abgasnormen in unterschiedlichen Themen der Fahrzeugelektronik\n- Erlernen des gefahrlosen Umgangs mit Komponenten und Fahrzeugen\n- Arbeiten mit modernen Diagnosesystemen zur Instandhaltung und Reparatur von modernsten Assistenz-, Komfort- und Sicherheitssystemen\n- Einblick in Fahrzeugvernetzung und Grundverständnis für Hard- und Software und fahrzeugspezifischen Applikationen\n\n\nWas wir dir bieten\n\n- Nach der Ausbildung besteht die Möglichkeit der Übernahme in ein unbefristetes Arbeitsverhältnis mit Beschäftigungssicherung bis 2033\n- 30 Tage Jahresurlaub bei einer 36 Stunden Woche und 5 Arbeitstagen\n- Attraktive und sichere Altersvorsorge (Arbeitgeberfinanziert)\n- Regelmäßige Schulungen und Weiterentwicklungsangebote\n- Dauerhaftes Schulungsangebot über LinkedIn Learning\n- Möglichkeit einer Hospitation bei einem unserer weiteren Standorte\n- Teilnahme an Azubiveranstaltungen\n- Umfassende Mobilitätsangebote – zum Beispiel Mitarbeiterleasing von PKW oder E-Bike Leasing\n- Betriebsinterne Krankenkasse\n- Bezahlte Freistellung für wichtige familiäre Ereignisse\n- Tariflich (Tarifgemeinschaft des bayerischen Kraftfahrzeuggewerbes) abgesichertes Urlaubs- und Weihnachtsgeld\n- Bereitstellung von personalisierter Arbeitskleidung (inkl. Wäscheservice)\n\n\nQualifikationen\n\nDas bringst du mit\n\n- Einen erfolgreichen Schulabschluss\n- Technisches Verständnis und handwerkliches Geschick\n- Überdurchschnittliche Kenntnisse in Mathematik und Physik\n- Sicheres Farbseh- und räumliches Vorstellungsvermögen\n- Interesse an Elektrik, Elektronik und Mechanik in Kraftfahrzeugen\n- Verantwortungsbewusste, sorgfältige und selbstständige Arbeitsweise\n\nWir leben bunt und vielfältig. Deswegen suchen wir dich als Mensch mit all deiner Individualität.  #MAKEYOURMOVE und bewirb Dich jetzt - wir freuen uns auf Dich! Zusätzliche Informationen:\n- Anlagen für Online-Bewerbung: ein Anschreiben, deinen Lebenslauf und die zwei letzten Zeugnisse\n- Interessierst du dich für unterschiedliche Ausbildungsberufe, teile uns deine Priorisierung im Anschreiben mit\n\n\nDu fühlst dich von unserem Angebot angesprochen und möchtest ein Teil des Teams werden? Dann bewirb dich direkt online mit deinen Bewerbungsunterlagen! Wir freuen uns insbesondere über Bewerbungen schwerbehinderter und ihnen gleichgestellter behinderter Menschen. Unter franz-peter.junginger@daimlertruck.com kannst du dich zudem an die Schwerbehindertenvertretung des Standorts wenden, der dich gerne im Bewerbungsprozess unterstützt.\n\nSolltest du bei deiner Online-Bewerbung Unterstützung benötigen, wir helfen dir gerne! Bei weiteren Fragen, wende dich an ausbildung-servicecenter@daimlertruck.com.\n\nÜbrigens: Wenn Du bereits in unserer Ausschreibung mit Du angesprochen wirst, nicht wundern – einfach zurückduzen. Im Service Center Neu-Ulm leben wir eine „Du-Kultur“. Falls Du Dich mit einem „Sie“ wohler fühlst – kleiner Hinweis genügt.\n\nUnsere Stärken: Hochmotivierte und begeisterte Mitarbeiter*innen mit großem Zusammengehörigkeitsgefühl und dem Willen, etwas zu bewegen. Denn bei Daimler Buses machst du den Unterschied. YOU MAKE US.\n\n#MAKEYOURMOVE und bewirb Dich jetzt – wir freuen uns auf Dich!\n\n\nUnsere Unternehmenskultur\nBei Daimler Truck fördern wir Vielfalt und stehen für eine inklusive Unternehmenskultur. Wir schätzen die individuellen Stärken unserer Mitarbeiter:innen, denn diese führen zur besten Teamleistung und somit zum Erfolg unseres Unternehmens. Inklusion und Chancengleichheit sind uns wichtig – ganz gleich woher Du kommst und wer Du bist. Wir freuen uns auf Bewerbungen von Personen aller Kulturen und Geschlechter, Eltern, Menschen mit Behinderungen und Menschen aus der LGBTIQ+ Community.\n\nBenefits\n-Kantine\n-Betriebsarzt\n-Mitarbeiter Events\n-Mitarbeiterrabatte möglich\n-Mitarbeiterhandy möglich\n-Gesundheitsmaßnahmen\n-Homeoffice möglich\n-Internetnutzung\n-Parkplatz\n-Gute Anbindung",
    "geforderterBildungsabschluss": "HAUPTSCHULABSCHLUSS",
    "arbeitszeitHeimarbeitTelearbeit": false,
    "arbeitszeitSchichtNachtWochenende": false,
    "arbeitszeitTeilzeitAbend": false,
    "arbeitszeitTeilzeitNachmittag": true,
    "arbeitszeitTeilzeitVormittag": true,
    "arbeitszeitTeilzeitFlexibel": false,
    "arbeitszeitVollzeit": true,
    "eintrittszeitraum": {
      "von": "2026-07-31"
    },
    "verguetungsangabe": "KEINE_ANGABEN",
    "vertragsdauer": "KEINE_ANGABE",
    "istGeringfuegigeBeschaeftigung": false,
    "istBehinderungGefordert": false,
    "stellenlokationen": [
      {
        "adresse": {
          "plz": "94447",
          "ort": "Plattling",
          "region": "BAYERN",
          "land": "DEUTSCHLAND"
        },
        "breite": 48.7858592,
        "laenge": 12.8949361
      }
    ],
    "chiffrenummer": "406686",
    "istPrivateArbeitsvermittlung": false,
    "istArbeitnehmerUeberlassung": false,
    "veroeffentlichungszeitraum": {
      "von": "2026-01-15"
    },
    "datumErsteVeroeffentlichung": "2026-01-15",
    "aenderungsdatum": "2026-02-09T15:31:36.240",
    "istBetreut": true,
    "hauptberuf": "Kraftfahrzeugmechatroniker/in - Nutzfahrzeugtechnik",
    "firma": "Daimler Truck AG",
    "arbeitgeberKundennummerHash": "ljXYoyZ9N9gd4nU54yvlp3E1PSmyo_hWN2jpOzW84tg=",
    "allianzpartnerUrl": "www.daimler-truck.com",
    "allianzpartnerName": "Daimler Truck AG",
    "referenznummer": "15707-1486969-2-S",
    "details_hash": "97bed448e8e60f0ebddeef09723e3b67ad828cd56d6f1fa75a38433fe202a5d0",
    "last_seen_remote_updated_at": "2026-02-09T15:31:36.240",
    "last_seen_remote_updated_at_dt": "2026-02-09 14:31:36.240000",
    "aenderungsdatum_dt": "2026-02-09 14:31:36.240000",
    "last_checked_at": "2026-02-23 01:04:35.375000",
    "created_at": "2026-02-23 01:04:35.375000",
    "updated_at": "2026-02-23 01:04:35.375000"
  }
}
```

### 5.3 referenznummer = `12456-1511718-1-S`
```json
{
  "referenznummer": "12456-1511718-1-S",
  "size": 7326,
  "stellenangebote": {
    "referenznummer": "12456-1511718-1-S",
    "aktuelleVeroeffentlichungsdatum": "2025-12-08",
    "arbeitgeber": "Deutsche Bahn AG",
    "arbeitsort": {
      "plz": "94447",
      "ort": "Plattling",
      "strasse": "null",
      "region": "Bayern",
      "land": "Deutschland",
      "koordinaten": {
        "lat": 48.785859189,
        "lon": 12.894936076
      },
      "entfernung": "0"
    },
    "beruf": "Elektroniker/in - Betriebstechnik",
    "created_at": "2026-02-23 01:02:16.426000",
    "eintrittsdatum": "2026-02-17",
    "is_active": true,
    "kundennummerHash": "yjIOvgjf5Crlw0chSxcAnUCWkGmLYyoftEbH3IL5NMc=",
    "meta_hash": "4a4df8077db1ba2a274957fc02c79586cd2824e20c6315e248b620f7c3784ae3",
    "modifikationsTimestamp": "2026-02-16T17:22:55.407",
    "modifikationsTimestamp_dt": "2026-02-16 16:22:55.407000",
    "refnr": "12456-1511718-1-S",
    "titel": "Schülerpraktikum Elektroniker:in für Betriebstechnik Nordbayern 2026",
    "updated_at": "2026-02-23 01:02:16.426000"
  },
  "jobdetails": {
    "stellenangebotsart": "PRAKTIKUM_TRAINEE",
    "stellenangebotsTitel": "Schülerpraktikum Elektroniker:in für Betriebstechnik Nordbayern 2026",
    "quereinstiegGeeignet": false,
    "stellenangebotsBeschreibung": "Die Deutsche Bahn ist eine der vielfältigsten Arbeitgeberinnen Deutschlands. Wir suchen jedes Jahr deutschlandweit über 5.000 Auszubildende in 50 Berufen, die mit uns gemeinsam noch besser werden wollen.\n\nWir suchen dich für ein Schülerpraktikum als Elektroniker:in (w/m/d) in Nürnberg, Plattling, Regensburg, Schwandorf, Weiden in der Oberpfalz oder Würzburg. Deinen Wunschzeitraum gibst du direkt im Bewerbungsprozess an. Schaue hinter die Kulissen der Deutschen Bahn, tausche dich mit unseren Azubis aus und finde heraus, ob eine Ausbildung bei uns zu dir passt.\nDie DB ist groß und wir haben Elektroniker:innen in verschiedenen Bereichen im Einsatz. Je nach zeitlicher Verfügbarkeit von Praktikumsplätzen bekommst du einen Einblick in den Arbeitsbereich von Elektroniker:innen an unseren Zügen, an unseren Schienen und Stromleitungen, an unseren Bahnhöfen oder in unseren Werken, in denen das normale Stromnetz in das Stromnetz der Bahn umgewandelt wird.\n\n\nWährend deines Schülerpraktikums:\n\n\n- Lernst du, wie elektrische Zugbauteile installiert und modernisiert werden\n\n- Siehst du, wie Systeme und Sicherheitseinrichtungen, z. B. die Anzeigen im Zug, programmiert und überprüft werden\n\n- Erlangst du Kenntnisse zu Mess-, Prüf- und Diagnoseverfahren sowie deren fachkundige Dokumentation\n\n- Machst du dich mit der Analyse und Beseitigung von Störungen vertraut\n\n\nAn unseren Schienen und Stromleitungen erhältst du außerdem Einblicke in diese Bereiche:\n\n\n- Wartung und Instandsetzung von Leit- und Sicherungstechnik, z. B. Stellwerkstechnik, Signale, Weichenmotoren und Bahnübergänge\n\n- Beseitigung von Störungen im Bahnbetrieb\n\n- Fachkundige Verkabelung von Signalen, Antennen und technischen Anlagen\n\n\nAn unseren Bahnhöfen erhältst du außerdem Einblicke in diese Bereiche:\n\n\n- Beseitigung von Störungen an elektronischen Anlagen, z. B. Fahrstühle, Rolltreppen und Hebebühnen\n\n- Einsatz von Mess-, Prüf- und Diagnoseverfahren für die Wartung und Instandhaltung der Anlagen\n\n\nAn unseren Zügen erhältst du außerdem Einblicke in diese Bereiche:\n\n\n- Programmieren und Überprüfen von elektronischen Bauteilen, wie z. B. Türen und Info-Displays\n\n- Einsatz von Mess-, Prüf- und Diagnoseverfahren für die Wartung und Instandsetzung von elektrischen Zugbauteile\n\n\nIn unseren Werken zur Umwandlung des Stromnetzes erhältst du außerdem Einblicke in diese Bereiche:\n\n\n- Überwachen und Installieren von technischen Anlagen, z. B. Schaltanlagen\n\n- Energieversorgung von Zügen, Bahnhöfen sowie Autos mit elektrischem Strom\n\n- Wartungs- und Inspektionsarbeiten sowie Prüfen und Beheben von Störungen\n\n\nDein Profil:\n\n\n- Du bist mindestens 14 Jahre alt und befindest dich in der 8. bis 13. Klasse\n- Die Berufswelt der DB findest du spannend\n\n- Du bringst Deutschkenntnisse mit, die mindestens dem Sprachniveau B2 entsprechen\n\n- Deine Arbeitsweise ist gewissenhaft und selbstständig\n\n\nBewirb dich jetzt online auf diese Ausschreibung und plane einen Vorlauf von mindestens 4 Wochen vor dem gewünschten Praktikumsbeginn ein. Deine vollständige Bewerbung enthält einen Lebenslauf, dein letztes vorhandenes Schulzeugnis und die Angabe deines Wunschzeitraums. Bei einem Pflichtpraktikum benötigen wir zusätzlich die Praktikumsvereinbarung deiner Schule.\n\nDas ist uns wichtig:\n\nUnser Anspruch ist es, allen Kandidat:innen bei gleicher Eignung die gleichen Chancen für den Einstieg bei uns zu bieten. Wir fördern aktiv die Vielfalt und das Miteinander in unseren Teams. Wir schätzen jede Bewerbung, unabhängig von Geschlecht, Nationalität, ethnischer und sozialer Herkunft, Religion, Weltanschauung, Behinderung, Alter, Dauer der Arbeitslosigkeit sowie sexueller Orientierung und Identität.\n\nDeine Vorteile\n* Dein Team arbeitet dich ein, begleitet dich und sorgt mit gezieltem Feedback für deine persönliche Weiterentwicklung.\n* Bei uns erlebst du die grüne Mobilität von morgen hautnah mit und entdeckst in unseren verschiedenen Arbeitsbereichen, wie wir uns für Klima- und Naturschutz einsetzen.\n* In einem vielfältigen Team begegnest du einigen unserer über 300.000 Kolleg:innen und hast so die Chance, deren Arbeitsweisen und unterschiedliche Perspektiven kennenzulernen.\n* Du kannst nicht nur einen Blick hinter die Kulissen der Deutschen Bahn werfen, sondern darfst selbst anpacken und dich ausprobieren. So findest du heraus, welcher Ausbildungsberuf wirklich zu dir passt!\n* Du willst aus erster Hand erfahren, wie eine Ausbildung bei der Deutschen Bahn wirklich ist. Während deiner Zeit bei uns kommst du mit unseren Azubis in den persönlichen Austausch und kannst ihnen alle deine Fragen stellen.\n\nChancengleichheit und selbstbestimmte Teilhabe Schwerbehinderter und Gleichgestellter sowie eine respektvolle Zusammenarbeit sind innerhalb des DB Konzerns fest verankerte Grundsätze. Deshalb werden schwerbehinderte und gleichgestellte Bewerber:innen bei gleicher Eignung bevorzugt berücksichtigt.",
    "geforderterBildungsabschluss": "NICHT_RELEVANT",
    "arbeitszeitVollzeit": true,
    "eintrittszeitraum": {
      "von": "2026-02-17"
    },
    "verguetungsangabe": "KEINE_ANGABEN",
    "vertragsdauer": "BEFRISTET",
    "befristungInMonaten": 1,
    "istGeringfuegigeBeschaeftigung": false,
    "istBehinderungGefordert": false,
    "stellenlokationen": [
      {
        "adresse": {
          "plz": "92421",
          "ort": "Schwandorf, Bayern",
          "region": "BAYERN",
          "land": "DEUTSCHLAND"
        },
        "breite": 49.314236264,
        "laenge": 12.094792921
      },
      {
        "adresse": {
          "plz": "93047",
          "ort": "Regensburg",
          "region": "BAYERN",
          "land": "DEUTSCHLAND"
        },
        "breite": 49.016902098,
        "laenge": 12.096319661
      },
      {
        "adresse": {
          "plz": "90461",
          "ort": "Nürnberg, Mittelfranken",
          "region": "BAYERN",
          "land": "DEUTSCHLAND"
        },
        "breite": 49.431528768,
        "laenge": 11.09031768
      },
      {
        "adresse": {
          "plz": "94447",
          "ort": "Plattling",
          "region": "BAYERN",
          "land": "DEUTSCHLAND"
        },
        "breite": 48.785859189,
        "laenge": 12.894936076
      },
      {
        "adresse": {
          "plz": "97070",
          "ort": "Würzburg",
          "region": "BAYERN",
          "land": "DEUTSCHLAND"
        },
        "breite": 49.794510073,
        "laenge": 9.93348898
      },
      {
        "adresse": {
          "plz": "92637",
          "ort": "Weiden in der Oberpfalz",
          "region": "BAYERN",
          "land": "DEUTSCHLAND"
        },
        "breite": 49.665348267,
        "laenge": 12.15828914
      }
    ],
    "chiffrenummer": "553660",
    "istPrivateArbeitsvermittlung": false,
    "istArbeitnehmerUeberlassung": false,
    "veroeffentlichungszeitraum": {
      "von": "2025-12-08"
    },
    "datumErsteVeroeffentlichung": "2025-12-08",
    "aenderungsdatum": "2026-02-16T17:22:55.407",
    "istBetreut": false,
    "hauptberuf": "Elektroniker/in - Betriebstechnik",
    "firma": "Deutsche Bahn AG",
    "arbeitgeberKundennummerHash": "yjIOvgjf5Crlw0chSxcAnUCWkGmLYyoftEbH3IL5NMc=",
    "allianzpartnerUrl": "www.deutschebahn.com/Karriere",
    "allianzpartnerName": "Deutsche Bahn AG",
    "referenznummer": "12456-1511718-1-S",
    "details_hash": "e0a91ac0b25b57a50646bbf4432b67016b8c3f1f3681431b7f0a46e4ce7d854a",
    "last_seen_remote_updated_at": "2026-02-16T17:22:55.407",
    "last_seen_remote_updated_at_dt": "2026-02-16 16:22:55.407000",
    "aenderungsdatum_dt": "2026-02-16 16:22:55.407000",
    "last_checked_at": "2026-02-23 01:05:34.891000",
    "created_at": "2026-02-23 01:05:34.891000",
    "updated_at": "2026-02-23 01:05:34.891000"
  }
}
```

