{ pkgs ? import <nixpkgs> {} }:
let
  kitchen = pkgs.poetry2nix.mkPoetryEnv { projectDir = ./.; };
in kitchen.env.overrideAttrs (oldAttrs: {
  buildInputs = [
    pkgs.poetry pkgs.redis
    pkgs.python39
    pkgs.python39Packages.black
    pkgs.python39Packages.mypy
    pkgs.python39Packages.isort
  ];
  shellHook = ''
    mkdir -p tools-links
    ln -fs `which mypy` tools-links/mypy
    ln -fs `which dmypy` tools-links/dmypy
  '';
})