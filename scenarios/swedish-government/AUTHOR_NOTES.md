# Author notes — Regeringsbildningen 2026

**AUTHOR-ONLY.** Denna fil är inte en Actor Resource och ska inte indexeras,
injiceras i Actor-context eller exponeras via Resource-API:t.

## 7.1.0-hypotes

7.1.0 ändrar två saker efter run 22 och inget annat:

- **Begränsad diplomati, 3 rundor per turn.** I run 22 stängde diplomatin tre av fyra gånger
  först när fyra partier nådde aktiveringstaket. Förhandlingens slut ska avgöras av rundgränsen,
  inte av ett skyddsräcke.
- **Internt godkännande som den ospelade osäkerheten.** Run 22 hade ingen enda sannolikhetsprövning
  på 32 Argument: alla avgörande partier spelas, röstningen är aritmetik och ett spelat partis
  beslut avgörs aldrig av tärning (D-118). Partistyrelser och riksdagsgrupper spelas inte, så
  deras godkännande av regeringsdeltagande, budget- och samarbetsavtal och brutna röda linjer är
  en legitim sannolikhetsfråga. Hypotesen är att detta ger tärningen arbete just vid de stora
  vägvalen utan att göra röstmatematiken osäker.


7.0.0 behåller v6:s spelregler, Actors och privata incitament men byter
förutsättningarna mot det verkliga läget vid informationsfrysen den
25 september 2026 kl. 12.00: det fastställda valresultatet (S 99, V 30),
statsministerns avgång och Anderssons sonderingsuppdrag. Förbättringen är
tidsmässig precision, inte mer dramatik.

Håll gränsen skarp mellan fastställt faktum, påstående, politisk position och
fiktivt spelantagande. Lägg inte händelsespecifika beteenderecept i de privata
briefarna: Borlängeärendet är offentlig information, och Actors avgör själva
vilken strategisk betydelse det har.

## V6-hypotes (bakgrund)

V6 försöker behålla v5:s spelregler men minska:

- duplicerad prompttext;
- överdriven saliens för regler som tidigare låg både i Effects och Resources;
- privata instruktioner som fungerar som färdiga beteenderecept;
- risken att ett partis privata brief blir en mini-policy-motor.

Detta är avsiktligt en scenarioförfining, inte en ny regeringsbildningsmodell.

## Vad en bra provider-run ska visa

En bra run behöver inte ge en viss regering. Den bör visa att:

1. mandatresursen faktiskt används när parlamentarisk matematik diskuteras;
2. Actors skiljer mellan regeringsdeltagande, stöd och tolerans;
3. flerpartsavtal inte skapas ensidigt;
4. större positionsskiften har en begriplig förhandlingsväg;
5. budgetvägen beaktas när ett slutligt paket gör anspråk på att vara fungerande;
6. flera koalitions-/toleransfamiljer är möjliga beroende på förhandlingarna;
7. Actors inte beter sig som om author-only material vore känt.

Från 7.1.0 dessutom:

8. varje Diplomacy-fas stängs av rundgränsen eller av att alla är klara, inte av
   aktiveringstaket;
9. minst några Argument om regeringsdeltagande, budgetavtal eller brutna röda linjer prövas
   som en sannolikhet mot internt godkännande, och ingen sannolikhet gäller ett annat spelat
   partis beslut eller 175-nej-summeringen.

## End-state evaluation rubric

Rubriken är **inte** en victory score och ska inte ges till Actors som ett
optimeringsmål. Bedöm dimensionerna separat.

### 1. Parlamentarisk giltighet

- **Stark:** relevanta röstpositioner är explicita och 175-nej-testet kan
  härledas deterministiskt från mandatdata och aktiva åtaganden.
- **Blandad:** paketet verkar möjligt men viktiga röstpositioner är fortfarande
  `OKÄND` eller oklara.
- **Svag:** utfallet bygger på att tystnad, antagen tolerans eller felaktig
  mandatmatematik räknas som stöd.

### 2. Avtalsintegritet

- **Stark:** påstådda flerpartsöverenskommelser stöds av varje berörd Actors
  egna uttryckliga åtaganden.
- **Blandad:** delar är etablerade men paketet beskriver mer än historiken
  faktiskt binder.
- **Svag:** en Actor har i praktiken skrivit andra Actors beslut åt dem.

### 3. Paketets fullständighet

Bedöm om slutläget gör följande begripligt:

- statsministerkandidat;
- regeringspartier;
- stöd-/toleranspartier;
- relevanta röstpositioner;
- centrala eftergifter/åtaganden;
- budgetväg.

### 4. Budgetmässig regeringsduglighet

- **Stark:** första budgetens stöd/tolerans och övergripande finanspolitiska ram
  är förenliga och uttryckligt accepterade där det krävs.
- **Blandad:** politisk vilja finns men viktiga budgetförutsättningar är oklara.
- **Svag:** regeringen klarar möjligen statsministerprövningen men saknar en
  begriplig första budgetväg.

### 5. Actor-måluppfyllelse

Bedöm varje Actor mot dess deklarerade objectives och privata fiktiva
incitament. Undvik att reducera detta till ett enda "vinnande parti".

### 6. Positionsrörelse

- Vilka Actors gjorde stora avsteg från offentlig utgångsposition?
- Fanns en förhandlingsväg, motprestation eller annan begriplig förankring?
- Var förändringen strategiskt motiverad eller ett abrupt agenthopp?

### 7. Epistemisk disciplin

Skilj mellan rättsligt faktum, offentligt påstående och Actor-tolkning.

- **Stark:** Actors använder Borlängeärendet och registreringsfelet som
  politiska argument men behandlar dem som det de är. V räknas med 30 mandat.
- **Blandad:** påståenden glider mot fakta i retoriken, men mandatmatematiken
  förblir korrekt.
- **Svag:** en Actor eller Umpiren behandlar ett påstående som fastställt, till
  exempel att V "bara har 29 legitima mandat", eller slår ihop de två
  valfrågorna till en berättelse.

Om ingen aktiv Effect ändrar resultatet gäller 99/70/62/25/30/22/22/19.

### 8. Residual instabilitet

- Finns aktiva oförenliga åtaganden?
- Är stöd/tolerans villkorat på något som fortfarande är oklart?
- Är paketet en möjlig regering eller bara en tillfällig mandatkonstruktion?

## Turn-order sensitivity

7.0.0 behåller samma fasta turn order som v5 och v6 för jämförbarhet:

`S → M → SD → C → V → KD → MP → L`

För analytiska experiment ska denna ordning behandlas som en **fixed condition**
eller uttrycklig treatment.

Om scenarioanalys blir viktig bör en separat känslighetsstudie använda cykliska
turn-order-varianter och mäta om senare position i argumentsekvensen förändrar
paketbildning, antal lyckade åtaganden eller slututfall.

Ändra inte turn order ad hoc mellan runs som ska jämföras.

## Real-name / pretrained-prior caveat

Detta scenario använder verkliga partinamn och frysta offentliga positioner.
En LLM kan därför använda latent förkunskap som inte kommer från Resources.

Det är acceptabelt för realism-, stress- och playtestbruk men försvagar rena
påståenden om scenario-isolerad information.

En senare analytisk kontroll kan skapa en strukturellt identisk anonymiserad
tvilling och jämföra beteende under samma mandat, objectives, regler och privata
spelantaganden.

## Leakage check

Provider-/benchmarkvalidering ska negativt verifiera att Actors **inte** kan
söka eller läsa:

- `AUTHOR_NOTES.md`;
- `README.md`;
- andra package-maintenance-filer som inte deklarerats under `resources:`.

Endast manifestdeklarerade Resources tillhör spelarnas informationsmiljö.
