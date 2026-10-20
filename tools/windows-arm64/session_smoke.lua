-- Native integration fixture: no microphone, speakers, or user session needed.
local source = ARDOUR.TransportRequestSource.TRS_UI
local function checkpoint(name)
    print("ARM64_SMOKE " .. name)
    io.stdout:flush()
end
local backend = AudioEngine:set_backend("None (Dummy)", "ARM64 validation", "")
assert(not backend:isnil(), "Dummy backend unavailable")
assert(backend:set_device_name("Sine Wave") == 0)
checkpoint("backend configured")
assert(load_session(arg[1], "session-smoke"))
checkpoint("session loaded")

local tracks = Session:new_audio_track(1, 2, ARDOUR.RouteGroup(), 1,
    "ARM64 synthetic capture", -1, ARDOUR.TrackMode.Normal, true)
assert(not tracks:empty())
local track = tracks:front()
checkpoint("track created")
assert(track:input():audio(0):connect("system:capture_1") == 0)
for channel = 0, 1 do
    local output = track:output():audio(channel)
    local master = Session:master_out():input():audio(channel)
    if not output:connected_to(master:name()) then
        assert(output:connect(master:name()) == 0)
    end
end
local effect = ARDOUR.LuaAPI.new_plugin(Session, "urn:ardour:a-comp",
    ARDOUR.PluginType.LV2, "")
assert(not effect:isnil(), "Bundled ARM64 ACE compressor unavailable")
assert(track:add_processor_by_index(effect, 0, nil, true) == 0)
effect:activate()
checkpoint("effect inserted")
track:rec_enable_control():set_value(1, PBD.GroupControlDisposition.NoGroup)
Session:maybe_enable_record(false)
Session:request_roll(source)
sleep(1.5)
checkpoint("record sleep complete")
assert(Session:actively_recording(), "Transport did not enter record")
Session:request_stop(false, false, source)
sleep(1)
checkpoint("stop sleep complete")
Session:disable_record(false, true)
assert(track:playlist():n_regions() > 0, "Recording produced no region")
Session:set_session_extents(Temporal.timepos_t(0), Temporal.timepos_t(48000))
Session:save_state("")
tracks = nil
track = nil
effect = nil
collectgarbage("collect")
close_session()
checkpoint("session saved and closed")

assert(load_session(arg[1], "session-smoke"))
checkpoint("session reloaded")
track = Session:route_by_name("ARM64 synthetic capture"):to_track()
assert(not track:isnil() and track:playlist():n_regions() > 0)
checkpoint("recorded region found")
Session:request_locate(0, true, ARDOUR.LocateTransportDisposition.MustRoll, source)
checkpoint("playback requested")
sleep(0.5)
checkpoint("playback sleep complete")
assert(Session:transport_sample() > 0, "Reloaded session did not play")
Session:request_stop(false, false, source)
checkpoint("playback stop requested")
sleep(1)
assert(Session:transport_stopped(), "Playback transport did not stop")
checkpoint("playback transport stopped")
Session:save_state("")
checkpoint("playback state saved")
track = nil
collectgarbage("collect")
close_session()
checkpoint("playback finished")
print("ARM64_RECORD_PLAY_RELOAD_EFFECT_PASSED")
io.stdout:flush()
