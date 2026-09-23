"""Pin the defaults of the Vertex endpoint builder.

The only production caller passes region and model explicitly, so a
mutated default never shows up there. The defaults are still part of
the function: omitting them must keep the global host and an empty model.
"""

from code_forge.llm_invoke import _build_vertex_url


class TestVertexUrlDefaults:
    def test_omitted_region_stays_on_the_global_host(self):
        url = _build_vertex_url("proj")
        assert url.startswith("https://aiplatform.googleapis.com/")
        assert "/locations/global/" in url

    def test_omitted_model_stays_empty(self):
        url = _build_vertex_url("proj", "global")
        assert url.endswith("/models/:rawPredict")
