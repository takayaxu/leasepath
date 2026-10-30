"""Compile only the thin bridge against the already built V100 llama library."""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    directory=ROOT/'.artifacts/d6-native';directory.mkdir(exist_ok=True)
    command=['c++','-std=c++17','-O2','-shared','-fPIC',str(ROOT/'patches/native_bridge.cpp')]
    for part in ('include','src','ggml/include'):command+=['-I'+str(ROOT/'.artifacts/llama-src'/part)]
    command+=['-L'+str(ROOT/'.artifacts/llama-build/bin'),'-Wl,-rpath,'+str(ROOT/'.artifacts/llama-build/bin'),
              '-lllama','-lggml','-lggml-base','-o',str(directory/'liblp_native.so')]
    subprocess.run(command,check=True)
    (directory/'build_command.json').write_text(json.dumps(command,indent=2)+'\n')
    print(str(directory/'liblp_native.so'))


if __name__=='__main__':main()
