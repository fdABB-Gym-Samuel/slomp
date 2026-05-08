{
  lib,
  stdenvNoCC,
  bun,
  cacert,
  nodejs,
  nodeModulesHash ? lib.fakeHash,
}:
let
  src = lib.cleanSourceWith {
    src = ../frontend;
    filter =
      path: type:
      let
        baseName = baseNameOf (toString path);
      in
      !(
        type == "directory"
        && (
          baseName == "node_modules"
          || baseName == ".svelte-kit"
          || baseName == "build"
          || baseName == ".vite"
        )
      );
  };

  nodeModules = stdenvNoCC.mkDerivation {
    pname = "slomp-frontend-node-modules";
    version = "0.1.0";

    src = lib.fileset.toSource {
      root = ../frontend;
      fileset = lib.fileset.unions [
        ../frontend/package.json
        ../frontend/bun.lock
      ];
    };

    nativeBuildInputs = [
      bun
      cacert
    ];

    dontConfigure = true;

    buildPhase = ''
      runHook preBuild
      export HOME=$TMPDIR
      bun install \
        --frozen-lockfile \
        --ignore-scripts \
        --no-progress
      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall
      mkdir -p $out
      cp -aR node_modules $out/
      runHook postInstall
    '';

    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = nodeModulesHash;
  };
in
stdenvNoCC.mkDerivation {
  pname = "slomp-frontend";
  version = "0.1.0";

  inherit src;

  nativeBuildInputs = [
    bun
    nodejs
  ];

  configurePhase = ''
    runHook preConfigure
    cp -aR ${nodeModules}/node_modules ./node_modules
    chmod -R u+w ./node_modules
    patchShebangs ./node_modules
    runHook postConfigure
  '';

  buildPhase = ''
    runHook preBuild
    export HOME=$TMPDIR
    bun run build
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/slomp-frontend
    cp -aR build/. $out/share/slomp-frontend/
    runHook postInstall
  '';

  meta = {
    description = "slomp SvelteKit frontend (static SPA build)";
  };
}
