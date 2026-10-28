# Ricerca: decompilare giochi Unity e portarli nel browser

Aggiornata al 27 ottobre 2026. Versioni e date verificate sulle pagine GitHub/NuGet ufficiali dove indicato;
ciò che non è stato possibile verificare è marcato **(non verificato)**.

> Nota di pubblicazione, 28 ottobre 2026: questi sono gli appunti storici della ricerca precedente.
> Versioni, misure esterne e risultati riportati non sono stati riverificati durante la pubblicazione di REU.
> La sezione 6 descrive quella sessione, non lo stato corrente dell'ambiente.
> Per requisiti attuali e verifiche della pubblicazione consulta [README](../README.it.md) e [TESTING.md](TESTING.md).

## 1. Il quadro in una pagina

Un gioco Unity compilato contiene due cose molto diverse:

1. **Dati serializzati** (scene, prefab, materiali, mesh, texture, audio, animazioni) in `globalgamemanagers`,
   `level*`, `sharedassets*.assets`, `data.unity3d`, bundle. AssetRipper può recuperarli, ma completezza e fedeltà
   dipendono dal gioco, dalla versione e dai singoli asset; vanno verificate sul progetto esportato.
2. **Codice**:
   - **Mono**: `*_Data/Managed/Assembly-CSharp.dll` è IL .NET completo. Si può decompilare (ILSpy) **oppure riusare
     così com'è**. Il riuso degli assembly originali è la strada "nativa": Unity WebGL compila quell'IL con IL2CPP
     in WebAssembly.
   - **IL2CPP**: `GameAssembly.dll` + `global-metadata.dat`. Il codice è già C++→macchina. Si recuperano nomi, tipi,
     firme e indirizzi, **non i corpi dei metodi**. Misura pubblica: 0,00% di corpi recuperati su MiSide e su un gioco Unity 6000.4
     ([il2cpp-wasm-teardown](https://github.com/clericall/il2cpp-wasm-teardown)); Cpp2IL in modalità ISIL "solleva" il 99,9%
     dei metodi in una forma leggibile ma non compilabile.

Da qui il verdetto dell'harness: **Mono = candidato al percorso automatico, IL2CPP = reimplementazione manuale.**
Il verdetto Mono non garantisce una build riuscita o un gioco funzionante nel browser.

## 2. Catalogo strumenti

### 2.1 Recupero progetto ed estrazione asset

| Strumento | Link | Stato / versione | Uso nell'harness |
|---|---|---|---|
| **AssetRipper** | [AssetRipper/AssetRipper](https://github.com/AssetRipper/AssetRipper) | 2.0.0 (24 ago 2026), GPL-3.0, attivo. Unity 3.5 → 6000.x | **Sì**: export progetto headless via API web locale |
| AssetRipper Premium | [assetripper.org](https://assetripper.org/) | funzioni premium: decompilazione shader, separazione mesh statiche (da `PremiumFeaturesPage.cs`) | opzionale |
| PeripheryExport (fork AssetRipper per White Knuckle) | [wiki White Knuckle](https://whiteknucklemodding.miraheze.org/wiki/Unity_decompilation) | fork specifico che rende il progetto funzionante | riferimento |
| Mrocznv/AssetRipper (fork Unity 6) | [Mrocznv/AssetRipper](https://github.com/Mrocznv/AssetRipper) | hard fork per export Unity 6 **(non verificato)** | riferimento |
| uTinyRipper | [mafaca/UtinyRipper](https://github.com/mafaca/UtinyRipper) | sospeso, Unity ≤ 2019 | no (sostituito da AssetRipper) |
| AssetStudio | [Perfare/AssetStudio](https://github.com/Perfare/AssetStudio) | archiviato | estrazione asset singoli |
| AssetStudioMod | [aelurum/AssetStudio](https://github.com/aelurum/AssetStudio) | fork mantenuto, CLI | estrazione asset singoli |
| UABEA | [nesrak1/UABEA](https://github.com/nesrak1/UABEA) | editor di bundle/assets | modifica asset puntuale |
| AssetsTools.NET | [nesrak1/AssetsTools.NET](https://github.com/nesrak1/AssetsTools.NET) | libreria .NET | script personalizzati |
| UnityPy | [K0lb3/UnityPy](https://github.com/K0lb3/UnityPy) | libreria Python | script personalizzati |
| USCSandbox | [nesrak1/USCSandbox](https://github.com/nesrak1/USCSandbox) | decompilatore shader DXBC→HLSL | ricostruzione shader (usato nel port di EtG) |
| Unity-Game-Decompiler | [gc1dk/unity-game-decompiler](https://github.com/gc1dk/unity-game-decompiler) | 1 commit, 5 stelle; IL2CPP solo scheletri (dichiarato nel README) | no |

### 2.2 Decompilatori .NET (giochi Mono)

| Strumento | Link | Note |
|---|---|---|
| **ILSpy / ilspycmd** | [icsharpcode/ILSpy](https://github.com/icsharpcode/ILSpy), [NuGet ilspycmd](https://www.nuget.org/packages/ilspycmd/) | MIT. ilspycmd 10.1.1.8388 fissato (ultima su NuGet: 11.1.0.9782). Comando `decompile` dell'harness |
| dnSpyEx | [dnSpyEx/dnSpy](https://github.com/dnSpyEx/dnSpy) | debugger + editor IL, fork mantenuto di dnSpy |
| dotPeek | [jetbrains.com/decompiler](https://www.jetbrains.com/decompiler/) | gratuito, closed source |
| de4dot | [de4dot/de4dot](https://github.com/de4dot/de4dot) | deoffuscatore (archiviato; esistono fork) |
| **Mono.Cecil** | [jbevain/cecil](https://github.com/jbevain/cecil) | 0.11.6: motore del patcher IL dell'harness |
| Harmony | [pardeike/Harmony](https://github.com/pardeike/Harmony) | patch a runtime (desktop/Mono); non usabile in IL2CPP WebGL, per questo l'harness patcha a monte |
| **ILVerify** | [NuGet dotnet-ilverify](https://www.nuget.org/packages/dotnet-ilverify/) | 10.0.14: comando `verify` (IL originale vs patchato) |

### 2.3 IL2CPP

| Strumento | Link | Stato / versione | Output |
|---|---|---|---|
| **Il2CppDumper** | [Perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) | v6.7.46; metadata v31 in pre-release v6.7.45 | `dump.cs`, DLL fittizie, script IDA/Ghidra, supporto WASM |
| **Cpp2IL** | [SamboyCoding/Cpp2IL](https://github.com/SamboyCoding/Cpp2IL) | 2022.1.0-pre-release.21 (22 feb 2026), MIT | `dummydll`, `isil`, `diffable-cs`, `dll_il_recovery` (corpi `throw null`) |
| Il2CppInspectorRedux | [LukeFZ/Il2CppInspectorRedux](https://github.com/LukeFZ/Il2CppInspectorRedux) | continuazione di Il2CppInspector | tipi, firme, progetti C++ di scaffolding |
| Il2CppInspector | [djkaty/Il2CppInspector](https://github.com/djkaty/Il2CppInspector) | sviluppo fermo | — |
| frida-il2cpp-bridge | [vfsfitvnm/frida-il2cpp-bridge](https://github.com/vfsfitvnm/frida-il2cpp-bridge) | ispezione a runtime con Frida | tracing dei metodi per capirne il comportamento |
| Ghidra / IDA | [ghidra-sre.org](https://ghidra-sre.org/) | con gli script di Il2CppDumper | lettura del codice nativo |
| wabt (`wasm2wat`, `wasm-decompile`) | [WebAssembly/wabt](https://github.com/WebAssembly/wabt) | per build WebGL esistenti (IL2CPP→wasm) | analisi `.wasm` |

### 2.4 Ispezione a runtime / modding (sul gioco desktop)

| Strumento | Link | Uso per il porting |
|---|---|---|
| BepInEx | [BepInEx/BepInEx](https://github.com/BepInEx/BepInEx) | caricare plugin nel gioco originale per osservare valori e flussi |
| MelonLoader | [LavaGang/MelonLoader](https://github.com/LavaGang/MelonLoader) | idem, supporta IL2CPP |
| UnityExplorer | [sinai-dev/UnityExplorer](https://github.com/sinai-dev/UnityExplorer) | ispettore scene/oggetti in-game (archiviato; esistono fork) |
| RuntimeUnityEditor | [ManlyMarco/RuntimeUnityEditor](https://github.com/ManlyMarco/RuntimeUnityEditor) | ispettore in-game |

### 2.5 Build WebGL

| Elemento | Dettaglio verificato |
|---|---|
| Unity batchmode | `-batchmode -quit -projectPath -buildTarget WebGL -executeMethod -logFile` ([manuale Unity 6](https://docs.unity3d.com/6000.0/Documentation/Manual/EditorCommandLineArguments.html)) |
| Archivio release Unity | `https://services.api.unity.com/unity/editor/release/v1/releases?version=X` → `shortRevision` (changeset) e deep link `unityhub://X/changeset` |
| Unity Hub CLI | `"Unity Hub.exe" -- --headless install --version X --changeset Y --module webgl`, `install-modules` |
| Formato `.data` | `UnityWebData1.0`: firma, `u32` dimensione header, voci `offset/size/len/nome`, poi i file; il loader li scompatta in `/` all'avvio |

## 3. Port reali da cui l'harness ha preso le idee

| Progetto | Link | Pipeline | Lezioni |
|---|---|---|---|
| **Enter the Gungeon → WebGL** (keekyslusus) | [EtG-WebGL](https://github.com/keekyslusus/EtG-WebGL) | AssetRipper 2.0.0 (*Dll Export Without Renaming*), Unity 2017.4.27f1 (stessa versione del gioco), patcher **Mono.Cecil** sulle DLL del gioco, shim per thread / preload bundle / salvataggi IndexedDB / gamepad, shader DXBC ricostruiti con USCSandbox, audio Wwise reimplementato in Web Audio | è esattamente l'approccio "nativo": DLL originali + patch IL minime. Hanno dovuto alzare lo stack di asm2wasm a 1 GB e saltare la deduplica asm.js per assembly enormi |
| **Overcooked → browser** (Deriv3d) | [OvercookedBrowserRewrite](https://github.com/Deriv3d/OvercookedBrowserRewrite) | AssetRipper 2.0.0 guidato da "chiamate web headless", Unity 2022.3.62f3, patch idempotenti con ancore `[webgl-port]`, stub per Steamworks.NET e XInput, shader sostitutivi, server con header per build compresse | mai pubblicare le build; patch idempotenti; serve.py con header giusti |
| **MiSide teardown** (clericall) | [il2cpp-wasm-teardown](https://github.com/clericall/il2cpp-wasm-teardown) | IL2CPP: AssetRipper + Cpp2IL; GUID degli script riparati (`remap-ugui.py`), header PE dei plugin riparati, comportamenti minimi riscritti a mano | misura: 0% di corpi C# recuperati da IL2CPP, 92–95% sui giochi Mono; i grafi `UnityEvent` serializzati sopravvivono |
| port2browser (infinuts) | [port2browser](https://github.com/infinuts/port2browser) | skill per agenti: 36 percorsi per motore | "una build compilata non è un port: serve la prova di giocabilità nel browser" |
| ULTRAKILL Prelude / Karlson (cake-logic) | [itch.io](https://cake-logic.itch.io/ultrakill-web-port) | da decompilazione | problemi noti: Ctrl+W chiude la scheda, outline disattivati, salvataggi instabili |
| genizy/web-port | [genizy/web-port](https://github.com/genizy/web-port) | 35 giochi (Cuphead da "decomp di SpanishFreddy e koi/_flixel", R.E.P.O, Ultrakill da 98Corbins) | ospitati pubblicamente come "unblocked games": non è il modello da seguire (copyright) |
| Catalogo dei port web | [Ultimate-Catalog-Of-Web-Game-Ports](https://github.com/Carter54git/Ultimate-Catalog-Of-Web-Game-Ports) | elenco con autori e sorgenti | utile per vedere se un gioco è già stato portato |

## 4. Persone e handle

La ricerca non ha trovato post su X (x.com non è indicizzato dagli strumenti di ricerca usati) che descrivano
pipeline di porting verificabili: gli handle X qui sotto **non sono verificati**. Quelli GitHub sì.

| Persona / gruppo | Dove | Cosa |
|---|---|---|
| ds5678 (Jeremy Pritts) | GitHub `ds5678`, sponsor di AssetRipper | maintainer AssetRipper |
| Sam Byass | GitHub [SamboyCoding](https://github.com/SamboyCoding) | autore Cpp2IL |
| Perfare | GitHub [Perfare](https://github.com/Perfare) | Il2CppDumper, AssetStudio |
| Katy Coe | GitHub [djkaty](https://github.com/djkaty) | Il2CppInspector |
| LukeFZ | GitHub [LukeFZ](https://github.com/LukeFZ) | Il2CppInspectorRedux |
| nesrak1 | GitHub [nesrak1](https://github.com/nesrak1) | UABEA, AssetsTools.NET, USCSandbox |
| aelurum | GitHub [aelurum](https://github.com/aelurum) | AssetStudioMod |
| K0lb3 | GitHub [K0lb3](https://github.com/K0lb3) | UnityPy |
| sinai-dev | GitHub [sinai-dev](https://github.com/sinai-dev) | UnityExplorer |
| keekyslusus | GitHub [keekyslusus](https://github.com/keekyslusus) | port WebGL di Enter the Gungeon |
| Deriv3d | GitHub [Deriv3d](https://github.com/Deriv3d) | port browser di Overcooked |
| clericall | GitHub [clericall](https://github.com/clericall) | teardown IL2CPP → wasm |
| genizy, 98Corbins, koi/_flixel, SpanishFreddy, burnedpopcorn, slqnt, irv77, bog/aukak | [genizy/web-port](https://github.com/genizy/web-port) | port "unblocked" di molti giochi |
| Reeyuki, wasmdotrip (wasm.rip), web-ports, webporting, NOTAHACKER9999, Toastiest64, woahhcrackers | [catalogo](https://github.com/Carter54git/Ultimate-Catalog-Of-Web-Game-Ports) | port di Inscryption, TABS, Slime Rancher, PEAK, Among Us, Hollow Knight, KSP… (repository di sole build, senza note tecniche) |

Ricerche da fare a mano su X: `AssetRipper WebGL`, `"web port" unity il2cpp`, `"Dll Export" AssetRipper`, `from:<handle> webgl`.

## 5. Ostacoli tecnici ricorrenti e come l'harness li gestisce

| Ostacolo | Visto in | Soluzione nell'harness |
|---|---|---|
| Thread C# assenti in WebGL | EtG, Overcooked | redirect IL `Thread`/`ThreadPool` → `ThreadShim` (main thread), loop segnalati da `scan` |
| Steamworks / XInput / DLL native | EtG, Overcooked, MiSide | stub IL automatici di tutti i P/Invoke non `__Internal`; relink `__Internal` per librerie con build WebGL |
| StreamingAssets letti in modo sincrono | EtG (preload bundle) | pack nel `.data` + redirect di `streamingAssetsPath`/`dataPath` + traduzione inversa per `UnityWebRequest` |
| Salvataggi | EtG, Overcooked | `GetFolderPath` → `persistentDataPath` (IDBFS) + `FS.syncfs` periodico e su `pagehide` |
| `Assembly-CSharp.dll` in conflitto con la compilazione Unity | EtG (rinomina manuale) | AssetRipper `DllExportWithRenaming` |
| Eccezioni null catturate dal codice desktop | itch.io (Unity 6 WebGL) | `exceptionSupport = FullWithoutStacktrace` |
| Stripping che rompe la reflection | generale | `link.xml` che preserva ogni assembly del gioco, stripping `Minimal` |
| Ctrl+W / Esc rubati dal browser | ULTRAKILL web port, Overcooked | template con fullscreen + Keyboard Lock API |
| Header di compressione sbagliati | Overcooked | `serve` con Content-Encoding, MIME wasm, Range, COOP/COEP |
| Shader custom (DXBC) | EtG | AssetRipper `ShaderExportMode=Decompile` (Premium) o USCSandbox: **manuale** |
| Audio Wwise / video Bink | EtG | rilevati come bloccanti: **manuale** |
| Socket UDP/TCP (multiplayer) | Stick Fight (test reale), MiSide | rilevati come bloccanti: serve relay WebSocket/WebRTC |

## 6. Validazione eseguita in questa sessione

- Patcher IL: gioco finto compilato, patchato ed eseguito → 11/11 comportamenti attesi (`dotnet/Tests/PatchRunner`).
- Gioco reale Mono (Stick Fight, Unity 5.6.7f1, dalla libreria Steam locale): `detect` → export AssetRipper headless
  (9 s caricamento, 92 s export, DLL originali preservate) → `patch` (893 import nativi stubbati, 14 call-site thread) →
  `verify` (ILVerify: nessun errore introdotto) → scan che individua correttamente il multiplayer UDP come bloccante.
- `detect` su 8 giochi Unity installati (Mono e IL2CPP, Unity 5.6 → 6000.3): versioni, backend e pipeline coerenti
  (un falso positivo HDRP dovuto alle stringhe `InternalsVisibleTo` nei metadata IL2CPP è stato trovato e corretto).
- Sorgenti C# iniettati compilati contro le DLL reali di Unity 6000.6.7f1 (runtime + editor) e Unity 5.6 (runtime).
- Ramo IL2CPP su Crab Game (Unity 2020.3): Il2CppDumper (`dump.cs`, `il2cpp.h`, script IDA/Ghidra) e Cpp2IL
  (`dummydll`, `isil`) completati.
- Review indipendente del codice: 12 rilievi, corretti tutti tranne due solo documentati (scritture in `dataPath`
  non persistenti; template 2020.1–2021.1 non provato).
- **Non verificato qui**: la build WebGL finale, perché l'Unity installato non ha una licenza attiva in batchmode
  (`No valid Unity Editor license found`) e Unity Hub non è installato.
