{
  lib,
  python312,
  stdenvNoCC,
  makeWrapper,
  ffmpeg-headless,
}:
let
  pythonEnv = python312.withPackages (
    ps: with ps; [
      fastapi
      uvicorn
      websockets
      httpx
      pydantic
      pydantic-settings
      python-dotenv
      redis
      mutagen
      pydub
      argon2-cffi
      asyncpg
    ]
  );
in
stdenvNoCC.mkDerivation {
  pname = "slomp-backend";
  version = "0.1.0";

  src = lib.cleanSourceWith {
    src = ../backend;
    filter =
      path: type:
      let
        baseName = baseNameOf (toString path);
      in
      !(
        type == "directory"
        && (
          baseName == "__pycache__"
          || baseName == ".ruff_cache"
          || baseName == ".pytest_cache"
          || baseName == ".venv"
        )
      )
      && !(lib.hasSuffix ".pyc" baseName);
  };

  nativeBuildInputs = [ makeWrapper ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/slomp-backend
    cp -r app $out/share/slomp-backend/

    mkdir -p $out/bin
    makeWrapper ${pythonEnv}/bin/uvicorn $out/bin/slomp-backend \
      --chdir $out/share/slomp-backend \
      --prefix PATH : ${lib.makeBinPath [ ffmpeg-headless ]} \
      --add-flags "app.main:app"

    runHook postInstall
  '';

  passthru = {
    inherit pythonEnv;
  };

  meta = {
    description = "slomp multiplayer Songless backend";
    mainProgram = "slomp-backend";
  };
}
