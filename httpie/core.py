"""This module provides the main functionality of HTTPie.

Invocation flow:

    1. Read, validate and process the input (args, `stdin`).
    2. Create and send a request.
    3. Stream, and possibly process and format, the requested parts
       of the request-response exchange.
    4. Simultaneously write to `stdout`
    5. Exit.

"""
from _socket import gaierror
import sys
import json
import errno
from pprint import pformat

import requests
import requests.auth
from requests.compat import str
from httpie import __version__ as httpie_version
from requests import __version__ as requests_version
from pygments import __version__ as pygments_version

from .cli import parser
from .models import Environment
from .output import output_stream, write
from . import EXIT


FORM = 'application/x-www-form-urlencoded; charset=utf-8'
JSON = 'application/json; charset=utf-8'


def get_requests_kwargs(args):
    """Send the request and return a `request.Response`."""

    auto_json = args.data and not args.form
    if args.json or auto_json:
        if 'Content-Type' not in args.headers and args.data:
            args.headers['Content-Type'] = JSON

        if 'Accept' not in args.headers:
            # Default Accept to JSON as well.
            args.headers['Accept'] = 'application/json'

        if isinstance(args.data, dict):
            # If not empty, serialize the data `dict` parsed from arguments.
            # Otherwise set it to `None` avoid sending "{}".
            args.data = json.dumps(args.data) if args.data else None

    elif args.form:
        if not args.files and 'Content-Type' not in args.headers:
            # If sending files, `requests` will set
            # the `Content-Type` for us.
            args.headers['Content-Type'] = FORM

    credentials = None
    if args.auth:
        credentials = {
            'basic': requests.auth.HTTPBasicAuth,
            'digest': requests.auth.HTTPDigestAuth,
        }[args.auth_type](args.auth.key, args.auth.value)

    kwargs = {
        'prefetch': False,
        'method': args.method.lower(),
        'url': args.url,
        'headers': args.headers,
        'data': args.data,
        'verify': {
            'yes': True,
            'no': False
        }.get(args.verify,args.verify),
        'timeout': args.timeout,
        'auth': credentials,
        'proxies': dict((p.key, p.value) for p in args.proxy),
        'files': args.files,
        'allow_redirects': args.allow_redirects,
        'params': args.params
    }

    return kwargs


def get_exist_status(code, allow_redirects=False):
    """Translate HTTP status code to exit status."""
    if 300 <= code <= 399 and not allow_redirects:
        # Redirect
        return EXIT.ERROR_HTTP_3XX
    elif 400 <= code <= 499:
        # Client Error
        return EXIT.ERROR_HTTP_4XX
    elif 500 <= code <= 599:
        # Server Error
        return EXIT.ERROR_HTTP_5XX
    else:
        return EXIT.OK


def main(args=sys.argv[1:], env=Environment()):
    """Run the main program and write the output to ``env.stdout``.

    Return exit status.

    """

    def error(msg, *args):
        msg = msg % args
        env.stderr.write('\nhttp: error: %s\n' % msg)

    debug = '--debug' in args
    traceback = debug or '--traceback' in args
    status = EXIT.OK

    if debug:
        sys.stderr.write('HTTPie version: %s\n' % httpie_version)
        sys.stderr.write('Requests version: %s\n' % requests_version)
        sys.stderr.write('Pygments version: %s\n' % pygments_version)

    try:
        args = parser.parse_args(args=args, env=env)
        kwargs = get_requests_kwargs(args)

        if args.debug:
            sys.stderr.write(
                '\n>>> requests.request(%s)\n\n' % pformat(kwargs))

        response = requests.request(**kwargs)

        if args.check_status:
            status = get_exist_status(response.status_code,
                                      args.allow_redirects)
            if status and not env.stdout_isatty:
                error('%s %s', response.raw.status, response.raw.reason)

        stream = output_stream(args, env, response.request, response)

        try:
            write(stream=stream,
                  outfile=env.stdout,
                  flush=env.stdout_isatty or args.stream)

        except IOError as e:
            if not traceback and e.errno == errno.EPIPE:
                # Ignore broken pipes unless --traceback.
                env.stderr.write('\n')
            else:
                raise

    except (KeyboardInterrupt, SystemExit):
        if traceback:
            raise
        env.stderr.write('\n')
        status = EXIT.ERROR
    except requests.Timeout:
        status = EXIT.ERROR_TIMEOUT
        error('Request timed out (%ss).', args.timeout)
    except Exception as e:
        # TODO: distinguish between expected and unexpected errors.
        #       network errors vs. bugs, etc.
        if traceback:
            raise
        error('%s: %s', type(e).__name__, str(e))
        status = EXIT.ERROR

    return status
