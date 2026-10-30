"""Private pure-compute entry point. Receives no database/session/LLM credentials."""
import os
import sys


def main():
    # Install Linux parent-death handling before importing numerical libraries.
    if sys.platform.startswith('linux'):
        import ctypes,signal
        ctypes.CDLL(None).prctl(1,signal.SIGKILL)
        if os.getppid()!=int(sys.argv[2]):raise SystemExit(2)
    from pathlib import Path
    from time import sleep
    from vibradiag.infrastructure.compute.codec import read,write,decode,encode,PROTOCOL
    directory=Path(sys.argv[1]).resolve();request=read(directory/'request.json')
    if set(request)!={'protocol','nonce','action','payload','probe'} or request['protocol']!=PROTOCOL or request['nonce']!=directory.name:raise ValueError('Invalid numeric request')
    write(directory/'ready.json',dict(pid=os.getpid(),environment_keys=sorted(os.environ),protocol=PROTOCOL))
    probe=request['probe']
    if probe=='hang':
        while True:sleep(.1)
    if probe=='crash':os._exit(17)
    if probe=='bad_result':write(directory/'response.json',{'bad':'result'});return
    if probe is not None:raise ValueError('Unknown controlled probe')
    from vibradiag.analysis.basic import compute_basic
    from vibradiag.analysis.csv_input import parse_csv
    from vibradiag.analysis.envelope import analyze_envelope
    from vibradiag.analysis.bearing_frequencies import calculate_bearing_frequencies
    from vibradiag.analysis.matching import match_frequency_evidence
    from vibradiag.analysis.comparison import compare_measurements
    from vibradiag.domain.measurement_errors import MeasurementError
    from vibradiag.domain.tool_results import ToolInputError
    action=request['action'];payload=decode(request['payload'],directory,'input')
    try:
        if action=='basic':result=compute_basic(parse_csv(payload['raw']).values,payload['sampling_rate'],payload['unit'])
        elif action=='envelope':result=analyze_envelope(parse_csv(payload['raw']).values,payload['parameters'])
        elif action=='frequency':result=calculate_bearing_frequencies(payload['parameters'])
        elif action=='matching':result=match_frequency_evidence(**payload)
        elif action=='comparison':result=compare_measurements(**payload)
        else:raise ValueError('Unknown numerical action')
        response=dict(protocol=PROTOCOL,nonce=request['nonce'],action=action,status='ok',payload=encode(result,directory,'output'))
    except (MeasurementError,ToolInputError) as error:
        response=dict(protocol=PROTOCOL,nonce=request['nonce'],action=action,status='error',code=error.code)
    write(directory/'response.json',response)


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit(18) from None
