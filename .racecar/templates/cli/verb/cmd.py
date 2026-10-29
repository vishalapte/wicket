def _cmd___VERB_ID__(args: argparse.Namespace) -> int:
    result = api.__FN__(__CALL__)
    return json_renderer.show(args, result, plaintext.print___VERB_ID__)
