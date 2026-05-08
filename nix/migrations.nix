{ stdenvNoCC }:
stdenvNoCC.mkDerivation {
  pname = "slomp-migrations";
  version = "0.1.0";

  src = ../migrations;

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out/migrations
    cp -a ./. $out/migrations/
    runHook postInstall
  '';

  meta = {
    description = "slomp database migrations (dbmate)";
  };
}
