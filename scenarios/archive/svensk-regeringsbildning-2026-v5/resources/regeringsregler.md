# Så fungerar regeringsbildningen och avtal i scenariot

Detta är den gemensamma regelresursen för AMAW-scenariot
**Regeringsbildningen 2026**.

Dokumentet skiljer mellan:

1. svensk parlamentarisk procedur som scenariot modellerar; och
2. AMAW-regler som behövs för att autonoma agenter inte ska kunna skapa andra
   spelares beslut genom ett ensidigt Argument.

## 1. Parlamentarisk grund

- Riksdagen har 349 ledamöter.
- Sverige använder negativ parlamentarism.
- Ett förslag till statsminister förkastas om minst **175 ledamöter röstar nej**.
- Om färre än 175 röstar nej är förslaget inte förkastat på denna grund.
- Det krävs alltså inte 175 ja-röster.

Mandatmatematiken ska alltid räknas från `valresultat_2026.csv`.

## 2. Två möjliga procedurlägen efter valet

Ett riksdagsval innebär inte automatiskt att den sittande statsministern avgår.

Scenariot måste därför före start ange vilket läge som faktiskt gäller.

### A. Den sittande statsministern har avgått

Regeringen sitter kvar som övergångsregering tills en ny regering tillträder.
Talmannen inleder arbetet med att ta fram ett förslag till ny statsminister och
för samtal med partiföreträdarna.

När talmannen presenterar sitt förslag anger talmannen också vilka partier som
är tänkta att ingå i regeringen.

### B. Den sittande statsministern sitter kvar

Den nyvalda riksdagen genomför den särskilda statsministerprövningen efter
valet. Om minst 175 ledamöter röstar nej måste statsministern avgå. Om färre
än 175 röstar nej kan statsministern sitta kvar.

Om statsministern faller går processen därefter över i talmannens arbete med
att ta fram ett nytt statsministerförslag.

## 3. Talmannens fyra försök

När en ny statsminister behöver utses är det talmannen som lägger fram förslag.

- Minst 175 nej-röster innebär att ett förslag förkastas.
- Om ett förslag förkastas kan talmannen lägga fram ett nytt.
- Talmannen har sammanlagt fyra försök.
- Om inget av de fyra förslagen godkänns ska extra val hållas inom tre månader.

Talmannen är **inte en spelad Actor** i denna scenarioversion.

## 4. Fyra röstlägen i spelet

För en namngiven statsministerkandidat ska ett spelat partis hållning beskrivas
som en av:

- **JA**
- **NEJ**
- **AVSTÅR**
- **OKÄND**

OKÄND är inte samma sak som AVSTÅR.

Tystnad, obesvarade meddelanden och frånvaro av ett bindande besked räknas som
OKÄND. Ett okänt parti får inte automatiskt användas för att få matematiken att
gå ihop.

## 5. Avtal kräver uttrycklig acceptans

Ett avtal mellan två eller flera spelade partier räknas som etablerat först när
samtliga berörda parter uttryckligen har accepterat de relevanta villkoren i
spelhistoriken.

Det räcker inte att:

- ett erbjudande har skickats;
- mottagaren inte har sagt nej;
- ett parti påstår att ett annat parti "är med";
- en agent anser att ett avtal vore logiskt;
- flera Actors upprepar samma påstående.

Privata meddelanden kan vara bevis för ett privat avtal. Om avtalet också är
offentligt är en separat fråga.

## 6. Regeringspaket i stället för enbart statsminister

Ett trovärdigt slutligt paket bör ange:

1. **statsministerkandidat**;
2. **regeringspartier**;
3. **eventuella stöd- eller toleranspartier**;
4. **uttryckliga röstpositioner** för de partier vars hållning behövs för att
   bedöma 175-nej-testet;
5. **centrala sakpolitiska eftergifter eller åtaganden**;
6. **en rimlig väg till budgetmässig regeringsduglighet**.

"Budgetmässig regeringsduglighet" betyder inte att scenariot ska simulera en
hel statsbudget. En trovärdig budgetväg kräver minst:

- vilka partier som avser stödja eller inte fälla regeringens första budget;
- en gemensamt accepterad övergripande finanspolitisk ram; och
- att inga aktiva Effects innehåller oförenliga budgetåtaganden.

En ensidig utsaga från ett parti etablerar inte en gemensam budgetväg.

## 6.1 Flerpartspaket är en härledd status

Ett flerpartspaket är låst först när aktiva Effects var för sig etablerar:

1. en namngiven statsministerkandidat;
2. varje avsedd regeringsparts egen acceptans;
3. relevanta uttryckliga `JA`, `NEJ` eller `AVSTÅR`;
4. stöd- eller toleransåtaganden som paketet åberopar; och
5. den trovärdiga budgetvägen ovan.

Ett enskilt partis Argument får etablera partiets egna åtaganden men inte skapa
andra partiers beslut. När samtliga förutsättningar finns följer paketstatusen
deterministiskt av dem.

## 7. Regeringsdeltagande, stöd och tolerans är olika saker

Håll isär:

- **regeringsdeltagande** — partiet ingår i regeringen;
- **aktivt stöd** — partiet stöder kandidaten eller ett specificerat paket;
- **tolerans** — partiet möjliggör lösningen utan att ingå i regeringen;
- **opposition** — partiet står utanför regeringen och har inte bundit sig till
  att tolerera den.

Ett avtal bör uttryckligen ange vilken relation som avses.

Ett besked om opposition eller om att inte nominera en kandidat binder bara
det partiets aktuella hållning. Det bevisar inte att alla andra alternativ är
parlamentariskt omöjliga.

## 8. Vad ett primärt Argument får göra

Ett primärt Argument bör i första hand beskriva ett **eget** steg.

Exempel:

- offentliggöra stöd för eller motstånd mot en kandidat;
- göra ett eget villkorat röstlöfte;
- acceptera eller lämna en redan förhandlad koalition;
- offentliggöra en egen sakpolitisk eftergift;
- dra tillbaka ett eget tidigare villkor;
- presentera ett flerpartsavtal som samtliga berörda parter redan har
  accepterat.

## 9. Vad ett Argument inte får göra

Ett parti får inte ensidigt fastställa att ett annat spelat parti:

- har accepterat ett erbjudande;
- har bytt sida;
- ska ingå i regeringen;
- har lovat JA, NEJ eller AVSTÅR;
- har dragit tillbaka ett villkor;
- är bundet av ett flerpartsavtal som det inte uttryckligen accepterat.

Om ett önskat Effect innehåller ett sådant påstående utan stöd i spelhistoriken
ska den delen inte behandlas som ett etablerat faktum.

## 10. Större positionsskiften behöver förankring

Offentliga positioner är utgångspunkter, inte fysiska lagar.

Ett spelat parti får ändra sin egen position, men ett stort skifte bör normalt
ha en synlig förhandlingsväg: sondering, villkor, erbjudanden eller annan
förankring tidigare i spelhistoriken.

Syftet är inte att göra positionsförändringar omöjliga utan att förhindra att
en agent hoppar direkt från en huvudposition till dess motsats utan politisk
kostnad eller förklaring.

## 11. Deterministisk mandatmatematik

När de relevanta röstpositionerna för en namngiven kandidat är kända är själva
riksdagsmatematiken inte osäker:

- minst 175 mandat uttryckligen på **NEJ** → kandidaten faller;
- färre än 175 mandat på **NEJ** → kandidaten är inte förkastad av
  175-nej-regeln.

Denna summering ska inte göras probabilistisk av Umpiren.

**Begränsning i nuvarande scenarioformat:** AMAW:s Matrix Game-adjudikation kan
fortfarande vara probabilistisk för politiska handlingar och förhandlingar.
Scenariot skapar därför uttryckliga röstpositioner och ett slutligt paket;
själva 175-nej-summeringen bör läsas deterministiskt från mandatfilen och de
etablerade röstpositionerna.

## 12. Intern förankring som spelmässig friktion

Varje Actor representerar ett helt riksdagsparti, inte en enskild politiker.

Därför bör större strategiska omläggningar behandlas som partibeslut. AMAW
simulerar inte partistyrelser eller riksdagsgrupper som egna Actors, men en
agent bör inte byta en central offentlig linje utan att spelhistoriken visar
någon form av förankring eller motprestation.

Detta är en spelabstraktion för att undvika överdrivet personliga eller
impulsiva förhandlingar.

## 13. Fyraturnsrytmen

### Turn 1 — sondering och paketbyggande

Aktörerna:

- läser mandatläget;
- undersöker möjliga regeringsunderlag;
- skickar privata eller offentliga erbjudanden;
- testar villkor;
- bygger preliminära paket.

Stora slutliga positionsskiften bör normalt inte dyka upp ur tomma intet här.

### Turn 2 — förhandling och motbud

Aktörerna prövar motbud, smalare alternativ och villkor för sakpolitik,
regeringsform och budgetväg.

### Turn 3 — åtaganden och konsolidering

Aktörerna förankrar större skiften och accepterar eller avslår konkreta villkor
med egna deltagande- och röstpositioner.

### Turn 4 — reaktioner och slutliga vägval

Aktörerna reagerar på etablerade åtaganden, slutför paket vars förutsättningar
finns och anger reservpositioner där de saknas.

Varje turn motsvarar 48 timmar. Rytmen är en förväntan, inte en regel som ger
senare Actors större rätt att binda andra. Turns är AMAW-spelstruktur, inte en
prognos över verklig svensk regeringsbildning.

## Officiell procedurreferens

För den verkliga svenska proceduren:

- Sveriges riksdag, **Så bildas regeringen**
  https://www.riksdagen.se/sv/sa-fungerar-riksdagen/demokrati/sa-bildas-regeringen/
- Sveriges riksdag, **Tre scenarier som kan uppstå efter valet**
  https://www.riksdagen.se/sv/aktuellt/valet-2026/tre-scenarier-som-kan-uppsta-efter-valet/
