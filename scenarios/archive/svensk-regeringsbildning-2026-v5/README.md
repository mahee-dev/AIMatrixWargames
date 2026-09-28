# AMAW-scenario: Regeringsbildningen 2026

Det här scenariot körs **när mandatfördelningen efter riksdagsvalet 2026 är
fastställd**, men innan regeringsfrågan är avgjord.

Det är ett Matrix Game för autonoma AI-agenter. Det är **inte en prognos** över
vilken regering som faktiskt kommer att bildas.

## Paketet

```text
scenario.yaml
README.md
resources/
  offentliga-positioner.md
  regeringsregler.md
  valresultat_2026.csv
```

## Vad v5 försöker modellera bättre

Den här versionen behandlar svensk regeringsbildning mindre som en fri
koalitionsauktion och mer som en förhandling mellan disciplinerade
partiorganisationer under negativ parlamentarism.

Ett bra slutresultat är därför inte bara "X blir statsminister". Det är ett
**regeringspaket** som åtminstone gör följande begripligt:

- statsministerkandidat;
- vilka partier som ska ingå i regeringen;
- vilka andra partier som aktivt stödjer eller tolererar lösningen;
- deras uttryckliga röstpositioner när de behövs för mandatmatematiken;
- centrala sakpolitiska eftergifter;
- en rimlig väg till budgetmässig regeringsduglighet.

V5 behåller v4:s fyra turns om 48 timmar, härledda flerpartspaket och exakta
krav på en trovärdig budgetväg. Den nya versionen fryser dessutom det senaste
mandatresultatet i den auktoritativa CSV-resursen, utan att skriva om de redan
körda v4-villkoren.

## Före första riktiga körningen

### 1. Kontrollera mandat endast i CSV-filen

Öppna:

```text
resources/valresultat_2026.csv
```

och kontrollera mandatantalet för varje parti som ska vara med i scenariot.

CSV-filen är scenariots **enda auktoritativa mandatkälla**. Kopiera inte samma
siffror till `starting_effects`.

### 2. Anpassa Actor-listan till den valda riksdagen

Om ett fördefinierat parti inte har mandat, ta bort det från:

- `actors`;
- `game.turn_order`;
- mandatfilen om raden inte längre används av scenariot.

Om ett annat parti behöver spelas krävs en riktig Actor-definition; en extra
CSV-rad räcker inte.

Kontrollera därefter att de representerade mandaten summerar till 349.

### 3. Kontrollera det fiktiva procedurläget

V5 väljer ett konkret spelantagande: den sittande statsministern har avgått,
övergångsregeringen sitter kvar och talmannen sonderar utan att ännu ha lagt
fram sitt första förslag. Ändra detta endast genom att skapa en ny
scenarioversion.

### 4. Validera innan dyr körning

Validera scenariopaketet med projektets vanliga scenario-/testsuite och gör
gärna en billig provkörning före dyrare modeller.

## Fyra turns om 48 timmar

Scenariot använder fyra turns och `max_cons_per_actor: 1`. Rytmen nedan är en
analytisk förväntan, inte en regel som hindrar tidiga bindande åtaganden.

### Turn 1 — sondering och paketbyggande

Agenternas huvudsakliga uppgift är att:

- läsa mandatläget;
- identifiera möjliga parlamentariska vägar;
- förhandla privat och offentligt;
- testa villkor och eftergifter;
- bygga preliminära regeringspaket.

### Turn 2 — förhandling och motbud

Agenternas huvudsakliga uppgift är att:

- pröva motbud och smalare alternativ;
- förhandla om sakpolitik, regeringsform och budgetväg;
- klarlägga vilka villkor som faktiskt kan accepteras.

### Turn 3 — åtaganden och konsolidering

- förankra större positionsskiften;
- acceptera eller avslå konkreta villkor;
- offentliggöra egna röst- och deltagandeåtaganden.

### Turn 4 — reaktioner och slutliga vägval

- reagera på etablerade åtaganden;
- slutföra möjliga paket;
- ange reservpositioner där nödvändiga förutsättningar saknas.

## Fyra röstlägen

För en konkret statsministerkandidat används:

- `JA`
- `NEJ`
- `AVSTÅR`
- `OKÄND`

Tystnad är alltid `OKÄND`, aldrig automatiskt `AVSTÅR`.

När de relevanta positionerna är kända är 175-nej-testet deterministiskt.
Umpiren ska inte göra själva mandatadditionens utfall probabilistiskt.

## Avtal måste finnas i spelhistoriken

Ett parti kan bara binda sig självt.

Flerpartsavtal, regeringsdeltagande, stöd, tolerans och röstlöften räknas som
etablerade först när de berörda spelade parterna uttryckligen har accepterat
dem.

Ett primärt Argument kan därför inte ensidigt skapa ett annat partis beslut.

Ett flerpartspaket är låst endast som en härledd status när de nödvändiga
åtagandena var för sig finns som aktiva Effects. Den Actor som råkar agera sist
får ingen särskild rätt att fastställa helheten.

## Intern förankring utan fler Actors

Scenariot simulerar partier som hela Actors. Partistyrelser, partiledningar och
riksdagsgrupper är inte separata agenter.

För att undvika orimligt snabba kast gäller ändå en enkel realismregel:

> Ett stort avsteg från en offentlig huvudposition bör normalt ha föregåtts av
> sondering, villkor, motprestation eller annan trovärdig förankring i
> spelhistoriken.

Detta är spelmässig friktion, inte ett påstående om någon verklig intern process
i ett specifikt parti.

## Budgetmässig regeringsduglighet

Scenariot simulerar inte en hel statsbudget.

En trovärdig budgetväg kräver minst vilka partier som avser stödja eller inte
fälla den första budgeten, en gemensamt accepterad övergripande finanspolitisk
ram och inga aktiva oförenliga budgetåtaganden. En ensidig utsaga räcker inte.

Det hindrar agenter från att optimera enbart för att "överleva 175 nej" och
därefter ignorera om regeringen kan fungera.

## Vad som är verkligt och vad som är konstruerat

`offentliga-positioner.md` är ett fryst offentligt scenariounderlag.

De privata uppgifterna i `scenario.yaml` är uttryckligen **fiktiva
spelantaganden**. De ska inte läsas som uppgifter om verkliga interna
partidiskussioner.

## Designmål

Scenariot försöker inte svara på vem som bör eller kommer att bilda regering.

Experimentfrågan är:

> Givet ett fast mandatresultat, samma institutionella regler och olika mål:
> vilka paket, erbjudanden, stödformer, röstpositioner och strategiska
> omsvängningar producerar autonoma agenter?

## Känd begränsning

AMAW:s Matrix Game-adjudikation är byggd för att bedöma osäkra handlingar.
En känd parlamentarisk mandataddition är däremot inte osäker.

V5 löser inte detta genom nya motorfunktioner. I stället instruerar scenariot
agenterna att skapa explicita röstpositioner och behandlar den slutliga
175-nej-summeringen som deterministisk när positionerna väl är kända.

Om detta scenario senare blir viktigt för benchmarkbruk är en liten
scenariooberoende "deterministic rule resolution"-mekanism i motorn sannolikt
renare än att lägga sådan logik i Umpire-prompten.
