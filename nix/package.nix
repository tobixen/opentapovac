# OpenTapoVac as a Nix package.  python-kasa comes from the TPAP branch on
# the tobixen fork (see docs/design.md, "Decisions"): the PR branch plus our
# fixes, pinned by revision.
{
  lib,
  python3Packages,
  fetchFromGitHub,
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
  python-kasa = python3Packages.python-kasa.overridePythonAttrs (old: {
    version = "0.10.2+tpap";
    src = fetchFromGitHub {
      owner = "tobixen";
      repo = "python-kasa";
      rev = "d7d4ed5b06340b774246087848e92ba5e5aac1e4"; # tpap-rv50-tls-fix
      hash = "sha256-HbbD6q1r+W969+9LjDmIRcAI9rA1hoRbbjP0wAC0+Gs=";
    };
    dependencies = (old.dependencies or old.propagatedBuildInputs or [ ]) ++ [
      ecdsa
      python3Packages.passlib
    ];
    # nixpkgs 25.05 has mashumaro 3.15; the branch asks for 3.20 (checked against the robot below)
    pythonRelaxDeps = [ "mashumaro" ];
    # the branch's test suite needs fixtures and extras the release didn't
    doCheck = false;
    pythonImportsCheck = [ "kasa" ];
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

  dependencies = [ python-kasa ] ++ (with python3Packages; [
    aiohttp
    argcomplete
    lz4
    pillow
    pyyaml
  ]);

  # pyproject names python-kasa by git URL; the pinned build above stands in for it
  pythonRemoveDeps = [ "python-kasa" ];

  nativeCheckInputs = with python3Packages; [
    pytestCheckHook
    pytest-asyncio
  ];

  pythonImportsCheck = [ "opentapovac" ];

  meta = {
    description = "Local control of a TP-Link Tapo robot vacuum: CLI, daemon and web page";
    homepage = "https://github.com/tobixen/opentapovac";
    license = lib.licenses.agpl3Plus;
    mainProgram = "opentapovac";
  };
}
