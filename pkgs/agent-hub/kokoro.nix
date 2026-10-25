{lib, buildPythonPackage, fetchPypi, hatchling, onnxruntime, phonemizer, numpy, espeak-ng}:
buildPythonPackage {
  pname = "kokoro-onnx";
  version = "0.6.1";
  pyproject = true;
  src = fetchPypi {
    pname = "kokoro_onnx";
    version = "0.6.1";
    sha256 = "7bbdb66dd53775f71088a99999a9aefdad240740daf54cb09410d8bb1e294e35";
  };
  build-system = [hatchling];
  dependencies = [onnxruntime phonemizer numpy];
  # nixpkgs supplies eSpeak and the custom-data-path patch in phonemizer 3.3.
  # The engine is pure Python; ONNX and NumPy already support our Python 3.14.
  pythonRelaxDeps = ["phonemizer"];
  pythonRemoveDeps = ["espeakng-loader"];
  postPatch = ''
    substituteInPlace pyproject.toml --replace-fail ",<3.14" ""
    substituteInPlace src/kokoro_onnx/tokenizer.py \
      --replace-fail "import espeakng_loader" "" \
      --replace-fail "espeakng_loader.get_data_path()" '"${espeak-ng}/share/espeak-ng-data"' \
      --replace-fail "espeakng_loader.get_library_path()" '"${lib.getLib espeak-ng}/lib/libespeak-ng.so"'
  '';
  pythonImportsCheck = ["kokoro_onnx"];
  doCheck = false;
  meta = {
    description = "Local Kokoro speech synthesis through ONNX Runtime";
    homepage = "https://github.com/thewh1teagle/kokoro-onnx";
    license = lib.licenses.mit;
  };
}
