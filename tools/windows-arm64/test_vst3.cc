// Small native VST3 fixture, using the interfaces already shipped with Ardour.
#include "vst3.h"
#include <atomic>
#include <cstring>
using namespace Steinberg;
using namespace Steinberg::Vst;
static const TUID class_id = INLINE_UID(0x41363454, 0x65737447, 0x61696E33, 0x00000001);
static bool same(const void* a, const void* b) { return std::memcmp(a, b, 16) == 0; }

class Effect final : public IComponent, public IAudioProcessor, public IEditController {
    std::atomic<uint32> references {1};
public:
    tresult PLUGIN_API queryInterface(const TUID id, void** object) override {
        *object = nullptr;
        if (same(id, IComponent_iid) || same(id, IPluginBase_iid) || same(id, FUnknown_iid))
            *object = static_cast<IComponent*>(this);
        else if (same(id, IAudioProcessor_iid)) *object = static_cast<IAudioProcessor*>(this);
        else if (same(id, IEditController_iid)) *object = static_cast<IEditController*>(this);
        if (!*object) return kNoInterface;
        addRef(); return kResultOk;
    }
    uint32 PLUGIN_API addRef() override { return ++references; }
    uint32 PLUGIN_API release() override { auto n = --references; if (!n) delete this; return n; }
    tresult PLUGIN_API initialize(FUnknown*) override { return kResultOk; }
    tresult PLUGIN_API terminate() override { return kResultOk; }
    tresult PLUGIN_API getControllerClassId(TUID) override { return kResultFalse; }
    tresult PLUGIN_API setIoMode(IoMode) override { return kResultOk; }
    int32 PLUGIN_API getBusCount(MediaType type, BusDirection) override { return type == kAudio ? 1 : 0; }
    tresult PLUGIN_API getBusInfo(MediaType type, BusDirection direction, int32 index, BusInfo& info) override {
        if (type != kAudio || index != 0) return kInvalidArgument;
        std::memset(&info, 0, sizeof(info));
        info.mediaType = kAudio; info.direction = direction; info.channelCount = 2;
        info.busType = kMain; info.flags = BusInfo::kDefaultActive;
        info.name[0] = 'A'; info.name[1] = '6'; info.name[2] = '4';
        return kResultOk;
    }
    tresult PLUGIN_API getRoutingInfo(RoutingInfo&, RoutingInfo&) override { return kNotImplemented; }
    tresult PLUGIN_API activateBus(MediaType, BusDirection, int32, TBool) override { return kResultOk; }
    tresult PLUGIN_API setActive(TBool) override { return kResultOk; }
    tresult PLUGIN_API setState(IBStream*) override { return kResultOk; }
    tresult PLUGIN_API getState(IBStream* stream) override {
        int32 value = 1; return stream->write(&value, sizeof(value), nullptr);
    }
    tresult PLUGIN_API setBusArrangements(SpeakerArrangement* inputs, int32 ins,
                                         SpeakerArrangement* outputs, int32 outs) override {
        return ins == 1 && outs == 1 && inputs[0] == 3 && outputs[0] == 3 ? kResultOk : kResultFalse;
    }
    tresult PLUGIN_API getBusArrangement(BusDirection, int32 index, SpeakerArrangement& arrangement) override {
        arrangement = 3; return index == 0 ? kResultOk : kInvalidArgument;
    }
    tresult PLUGIN_API canProcessSampleSize(int32 size) override { return size == kSample32 ? kResultOk : kResultFalse; }
    uint32 PLUGIN_API getLatencySamples() override { return 0; }
    tresult PLUGIN_API setupProcessing(ProcessSetup&) override { return kResultOk; }
    tresult PLUGIN_API setProcessing(TBool) override { return kResultOk; }
    tresult PLUGIN_API process(ProcessData& data) override {
        if (data.numInputs && data.numOutputs && data.symbolicSampleSize == kSample32) {
            for (int channel = 0; channel < data.outputs[0].numChannels; ++channel)
                for (int i = 0; i < data.numSamples; ++i)
                    data.outputs[0].channelBuffers32[channel][i] = data.inputs[0].channelBuffers32[channel][i] * 0.5f;
            data.outputs[0].silenceFlags = data.inputs[0].silenceFlags;
        }
        return kResultOk;
    }
    uint32 PLUGIN_API getTailSamples() override { return 0; }
    tresult PLUGIN_API setComponentState(IBStream*) override { return kResultOk; }
    int32 PLUGIN_API getParameterCount() override { return 0; }
    tresult PLUGIN_API getParameterInfo(int32, ParameterInfo&) override { return kInvalidArgument; }
    tresult PLUGIN_API getParamStringByValue(ParamID, ParamValue, String128) override { return kInvalidArgument; }
    tresult PLUGIN_API getParamValueByString(ParamID, TChar*, ParamValue&) override { return kInvalidArgument; }
    ParamValue PLUGIN_API normalizedParamToPlain(ParamID, ParamValue value) override { return value; }
    ParamValue PLUGIN_API plainParamToNormalized(ParamID, ParamValue value) override { return value; }
    ParamValue PLUGIN_API getParamNormalized(ParamID) override { return 0; }
    tresult PLUGIN_API setParamNormalized(ParamID, ParamValue) override { return kInvalidArgument; }
    tresult PLUGIN_API setComponentHandler(IComponentHandler*) override { return kResultOk; }
    IPlugView* PLUGIN_API createView(FIDString) override { return nullptr; }
};

class Factory final : public IPluginFactory {
public:
    tresult PLUGIN_API queryInterface(const TUID id, void** object) override {
        *object = nullptr;
        if (!same(id, IPluginFactory_iid) && !same(id, FUnknown_iid)) return kNoInterface;
        *object = this; addRef(); return kResultOk;
    }
    uint32 PLUGIN_API addRef() override { return 2; }
    uint32 PLUGIN_API release() override { return 1; } // Static factory.
    tresult PLUGIN_API getFactoryInfo(PFactoryInfo* info) override {
        std::memset(info, 0, sizeof(*info));
        std::strcpy(info->vendor, "Ardour build validation");
        return kResultOk;
    }
    int32 PLUGIN_API countClasses() override { return 1; }
    tresult PLUGIN_API getClassInfo(int32 index, PClassInfo* info) override {
        if (index != 0) return kInvalidArgument;
        std::memset(info, 0, sizeof(*info));
        std::memcpy(info->cid, class_id, 16); info->cardinality = PClassInfo::kManyInstances;
        std::strcpy(info->category, kVstAudioEffectClass);
        std::strcpy(info->name, "ARM64 Validation Gain VST3");
        return kResultOk;
    }
    tresult PLUGIN_API createInstance(FIDString cid, FIDString iid, void** object) override {
        *object = nullptr;
        if (!same(cid, class_id)) return kInvalidArgument;
        auto* effect = new Effect;
        auto result = effect->queryInterface(iid, object);
        effect->release(); return result;
    }
};

extern "C" __declspec(dllexport) IPluginFactory* PLUGIN_API GetPluginFactory() {
    static Factory factory; return &factory;
}
extern "C" __declspec(dllexport) bool InitDll() { return true; }
extern "C" __declspec(dllexport) bool ExitDll() { return true; }
