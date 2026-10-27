# -*- coding: utf-8 -*-
"""
Correctif du module CS2 de CS Demo Manager (licence MIT, AkiVer), appliqué avant la compilation.

Depuis la mise à jour CS2 1.41.7.6, la lecture d'une démo n'appelle plus ClientFullyConnect : le module
n'accroche jamais FrameStageNotify, donc aucune commande (saut au round, startmovie, quit) n'est exécutée
et CS2 lit toute la démo (https://github.com/akiver/cs-demo-manager/issues/1458).
Correctif proposé dans ce ticket : accrocher FrameStageNotify dès que client.dll expose Source2Client002
(fil qui vérifie toutes les 50 ms, 90 s maximum), sans attendre ClientFullyConnect.

Usage : python corriger_module.py <chemin de main.cpp>
"""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()


def rep(old, new):
    global src
    if src.count(old) != 1:
        sys.exit(f"Correctif impossible : repère introuvable ou multiple :\n{old}")
    src = src.replace(old, new)


HELPERS = r'''
// --- HexaClip : accroche de FrameStageNotify sans attendre ClientFullyConnect (CS2 1.41.7.6+) ---
std::mutex hexaHookMutex;
bool hexaFrameHooked = false;

static ISource2Client* HexaFindClient()
{
#if defined _WIN32
    HMODULE mod = GetModuleHandleA("client.dll");
    if (mod == NULL) {
        return NULL;
    }
    CreateInterfaceFn fn = (CreateInterfaceFn)GetProcAddress(mod, "CreateInterface");
    if (fn == NULL) {
        return NULL;
    }
    return (ISource2Client*)fn("Source2Client002", NULL);
#else
    return factory != NULL ? (ISource2Client*)factory("Source2Client002", NULL) : NULL;
#endif
}

static bool HexaTryHookFrameStageNotify()
{
    std::lock_guard<std::mutex> lock(hexaHookMutex);
    if (hexaFrameHooked) {
        return true;
    }
    ISource2Client* c = HexaFindClient();
    if (c == NULL) {
        return false;
    }
    client = c;
    Log("Hooking FrameStageNotify");
    auto vtable = *(void***)client;
    originalFrameStageNotify = (FrameStageNotifyFn)vtable[36];
    PatchVTableEntry(vtable, 36, (void*)&NewFrameStageNotify);
    hexaFrameHooked = true;
    Log("Hooked FrameStageNotify");
    QueueEngineCommand("demo_ui_mode 0");
    QueueEngineCommand("sv_cheats 1");
    return true;
}

static void HexaHookPoller()
{
    for (int i = 0; i < 1800 && !isQuitting; i++) {
        if (HexaTryHookFrameStageNotify()) {
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    if (!hexaFrameHooked) {
        Log("FrameStageNotify hook timeout");
    }
}

'''

rep("bool Connect(IAppSystem* appSystem, CreateInterfaceFn factoryFn)\n",
    HELPERS + "bool Connect(IAppSystem* appSystem, CreateInterfaceFn factoryFn)\n")

rep("    RestoreGameinfoFile();\n\n    return result;\n",
    "    RestoreGameinfoFile();\n\n    std::thread(HexaHookPoller).detach();\n\n    return result;\n")

start = src.index("void NewClientFullyConnect(void* thisptr, int playerSlot)\n{")
end = src.index("EXPORT void* CreateInterface(")
src = src[:start] + '''void NewClientFullyConnect(void* thisptr, int playerSlot)
{
    Log("ClientFullyConnect: playerSlot=%d", playerSlot);
    HexaTryHookFrameStageNotify();
    originalClientFullyConnect(thisptr, playerSlot);
}

''' + src[end:]

open(path, "w", encoding="utf-8").write(src)
print("Module CS2 corrigé :", path)
