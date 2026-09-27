# OpenTapoVac as a Nix package.  python-kasa is the nixpkgs release; the
# TPAP transport is vendored in src/opentapovac/_tpap.py (see
# docs/design.md, "Decisions").
{
  lib,
  python3Packages,
  # not `src`/`version`: callPackage would fill those from same-named packages
  opentapovacSrc ? lib.cleanSource ../.,
  opentapovacVersion ? "0.0.0+local",
}:
let
  # nixpkgs flags ecdsa for CVE-2024-23342 (a timing side channel, "won't fix"
  # upstream).  TPAP needs it for the SPAKE2+ handshake; the exposure is timing
  # measurements of the handshake with the robot, which here runs over the
  # LAN or WireGuard.  Allowed for this package only.
  ecdsa = python3Packages.ecdsa.overridePythonAttrs (old: {
    meta = old.meta // { knownVulnerabilities = [ ]; };
  });
in
python3Packages.buildPythonApplication {
  pname = "opentapovac";
  version = opentapovacVersion;
  src = opentapovacSrc;
  pyproject = true;

  env.SETUPTOOLS_SCM_PRETEND_VERSION = opentapovacVersion;
  # the git describe options need a newer setuptools-scm, and the version is given above
  postPatch = ''
    sed -i '/^raw-options = /d' pyproject.toml
  '';

  build-system = with python3Packages; [
    hatchling
    hatch-vcs
    argcomplete
  ];

  dependencies = [ ecdsa ] ++ (with python3Packages; [
    python-kasa
    passlib
    aiohttp
    argcomplete
    lz4
    pillow
    pyyaml
  ]);

  nativeCheckInputs = with python3Packages; [
    pytestCheckHook
    pytest-asyncio
  ];

  pythonImportsCheck = [ "opentapovac" ];

  meta = {
    description = "Local control of a TP-Link Tapo robot vacuum: CLI, daemon and web page";
    homepage = "https://github.com/tobixen/opentapovac";
    license = with lib.licenses; [ agpl3Plus gpl3Plus ];
    mainProgram = "opentapovac";
  };
}
