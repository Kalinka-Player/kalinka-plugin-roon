from kalinka_plugin_roon.metadata import alsa_output, playback_state


def test_alsa_reports_output_only_not_source_depth(tmp_path):
    path = tmp_path / "hw_params"
    path.write_text(
        "access: RW_INTERLEAVED\nformat: S32_LE\nchannels: 2\nrate: 96000 (96000/1)\n"
    )
    output = alsa_output(path)
    state = playback_state({"state": "playing"}, output=output)
    assert state.audio_info.output.sample_rate == 96000
    assert state.audio_info.output.channels == 2
    assert state.audio_info.output.bits_per_sample == 0
    assert state.audio_info.sample_rate == 0
    assert not state.audio_info.output.lossless_path
    path.write_text("closed\n")
    assert alsa_output(path) is None
    assert alsa_output(tmp_path / "absent") is None


def test_radio_unknown_duration_missing_metadata_and_negative_seek():
    state = playback_state(
        {
            "state": "loading",
            "now_playing": {
                "one_line": {"line1": "Radio"},
                "seek_position": -1,
            },
        }
    )
    assert state.current_track.title == "Radio"
    assert state.current_track.duration == 0
    assert state.position == 0
    assert state.current_track.album.image is None
