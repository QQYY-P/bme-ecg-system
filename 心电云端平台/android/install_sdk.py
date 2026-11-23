"""Install only the SDK components covered by the user's explicit license approval."""
from pathlib import Path
import argparse
import subprocess
import sys

REQUIRED=(
    'platforms/android-35/android.jar',
    'build-tools/35.0.0/aapt2.exe',
    'build-tools/35.0.0/zipalign.exe',
    'build-tools/35.0.0/lib/d8.jar',
    'build-tools/35.0.0/lib/apksigner.jar',
)

def install(sdk: Path, log: Path):
    if all((sdk/name).is_file() for name in REQUIRED):
        print('Required SDK components are already installed.',flush=True)
        return
    manager=sdk/'cmdline-tools/latest/bin/sdkmanager.bat'
    command=[str(manager),'--sdk_root='+str(sdk),'platforms;android-35','build-tools;35.0.0']
    # Windows PowerShell 5 UTF-8 pipelines can prepend a BOM to "y". Send raw ASCII
    # directly so SDK Manager receives the already-approved license confirmation.
    print('Installing Android 35 and Build Tools 35.0.0. Download details: '+str(log),flush=True)
    result=subprocess.run(command,input=b'y\n'*32,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    output=result.stdout.decode('utf-8',errors='replace')
    log.parent.mkdir(parents=True,exist_ok=True)
    log.write_text(output,encoding='utf-8')
    print(output,flush=True)
    if result.returncode:
        raise RuntimeError('SDK Manager failed; see '+str(log))
    missing=[name for name in REQUIRED if not (sdk/name).is_file()]
    if missing:
        raise RuntimeError('SDK Manager did not install required components: '+', '.join(missing)+'. See '+str(log))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk',type=Path,required=True)
    parser.add_argument('--accept-sdk-license',action='store_true')
    args=parser.parse_args()
    if not args.accept_sdk_license:
        parser.error('The Android SDK license must be explicitly accepted before installation.')
    install(args.sdk.resolve(),Path(__file__).resolve().parent/'work/sdk-install.log')

if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SDK installation failed: '+str(error),file=sys.stderr)
        sys.exit(1)
