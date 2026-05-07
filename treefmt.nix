{ ... }:
{
  projectRootFile = "flake.nix";

  programs.nixfmt.enable = true;
  programs.ruff-format.enable = true;
  programs.ruff-check.enable = true;
  programs.prettier.enable = true;

  settings.formatter.prettier.includes = [
    "*.json"
    "*.md"
    "*.yaml"
    "*.yml"
  ];
}
