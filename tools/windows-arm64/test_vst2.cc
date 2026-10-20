// Minimal native ARM64 effect used only by the scanner/hosting tests.
#include "vestige/vestige.h"
#include <cstring>

struct TestEffect { AEffect effect {}; float gain = 0.5f; };

static intptr_t dispatch(AEffect* effect, int opcode, int, intptr_t, void* data, float)
{
    switch (opcode) {
    case effClose: delete reinterpret_cast<TestEffect*>(effect); return 1;
    case effGetEffectName:
    case effGetProductString: std::strcpy(static_cast<char*>(data), "ARM64 Validation Gain"); return 1;
    case effGetVendorString: std::strcpy(static_cast<char*>(data), "Ardour build validation"); return 1;
    case effGetProgramName: std::strcpy(static_cast<char*>(data), "Default"); return 1;
    case effGetParamName: std::strcpy(static_cast<char*>(data), "Gain"); return 1;
    case effCanBeAutomated: return 1;
    case effGetPlugCategory: return 1;
    case effGetVstVersion: return 2400;
    case effGetVendorVersion: return 1;
    default: return 0;
    }
}

static void process(AEffect* effect, float** input, float** output, int frames)
{
    const float gain = reinterpret_cast<TestEffect*>(effect)->gain;
    for (int channel = 0; channel < 2; ++channel)
        for (int i = 0; i < frames; ++i)
            output[channel][i] = input[channel][i] * gain;
}

extern "C" __declspec(dllexport) AEffect* VSTPluginMain(audioMasterCallback host)
{
    if (!host || !host(nullptr, audioMasterVersion, 0, 0, nullptr, 0)) return nullptr;
    auto* state = new TestEffect;
    auto& effect = state->effect;
    effect.magic = kEffectMagic;
    effect.dispatcher = dispatch;
    effect.process = effect.processReplacing = process;
    effect.setParameter = [](AEffect* e, int, float value) { reinterpret_cast<TestEffect*>(e)->gain = value; };
    effect.getParameter = [](AEffect* e, int) { return reinterpret_cast<TestEffect*>(e)->gain; };
    effect.numInputs = effect.numOutputs = 2;
    effect.numPrograms = effect.numParams = 1;
    effect.flags = effFlagsCanReplacing;
    effect.uniqueID = CCONST('A', '6', '4', 'T');
    effect.version = 1;
    return &effect;
}
