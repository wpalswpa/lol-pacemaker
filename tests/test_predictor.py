from src.predictor import LocalOnnxPredictor, ScoreboardState


def test_local_onnx_inference_runs_without_server():
    predictor = LocalOnnxPredictor()
    even = ScoreboardState(my_side="blue", blue_avg_level=7.0, red_avg_level=7.0)
    ahead = ScoreboardState(
        my_side="blue",
        blue_first_blood=1,
        blue_kills=8,
        red_kills=2,
        blue_cs=380,
        red_cs=310,
        blue_avg_level=8.5,
        red_avg_level=7.2,
        blue_dragons=2,
        blue_towers=2,
    )
    p_even = predictor.predict_win_prob(even)
    p_ahead = predictor.predict_win_prob(ahead)
    assert 0.0 <= p_even <= 1.0
    assert 0.0 <= p_ahead <= 1.0
    assert p_ahead > p_even + 0.15


def test_red_side_mirrors_probability():
    predictor = LocalOnnxPredictor()
    blue_state = ScoreboardState(my_side="blue", blue_kills=10, red_kills=3, blue_avg_level=8.0, red_avg_level=7.0)
    red_state = blue_state.model_copy(update={"my_side": "red"})
    assert abs((predictor.predict_win_prob(blue_state) + predictor.predict_win_prob(red_state)) - 1.0) < 1e-5
