#!/usr/bin/env python

#
# Copyright 2018 Fabian Binder comNET GmbH <fabian.binder@comnetgmbh.com>
#
# This file is part of HTTPySim.
#
# HTTPySim is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# HTTPySim is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with HTTPySim.  If not, see <http://www.gnu.org/licenses/>.
#

from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from urlparse import urlparse
import StringIO, argparse, os, sys, urllib2, gzip, io, yaml, time, re

rules = {'method'  : 'match',
         'headers' : 'match',
         'uri' : {'scheme'     : 'match',
                  'host'       : 'match',
                  'path'       : 'match',
                  'query_form' : 'match'},
        }

def print_verbose(text): # Simple verbose output
    if args.verbose:
        print text


def decompress(data): # Decompress gzip data
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as fh:
        try:
            unzipped = fh.read()
        except struct.error:
            return None
    return unzipped


def dump_data(hostname, url, method, req_headers, resp_headers, code, data, rules): # Dump json data in yaml format
    yamldata = {'request':
                   {
                    'class': 'HTTP::Request',
                    'uri': url,
                    'fields': {'content': '', 'headers': req_headers},
                    'method': method,
                    'uri': url
                   },
                'response':
                   {
                    'class': 'HTTP::Response',
                    'fields': {'code': code, 'content': data, 'headers': resp_headers},
                   },
                 'rules': rules
               }
    time_now = int(time.time())
    if not os.path.exists('templates'):
            os.makedirs('templates')
    with io.open('templates/%s_%s.yaml' % (hostname, time_now), 'w', encoding='utf8') as outfile:
        yaml.dump(yamldata, outfile, default_flow_style=False, allow_unicode=False)
    return 'templates/%s_%s.yaml' % (hostname, time_now)


def get_matching_template(method, hostname, headers, request_url): # Get template that matches the request. The matching rule is included in the template.
    for file in os.listdir('templates'):
        with io.open('templates/%s' % file) as template:
            print_verbose('Checking template file {} ...'.format(file))
            template = yaml.load(template.read())
            rules = template['rules']
            matches = True

            for item, value, value_templ in \
                [('headers', headers, template['request'].get('fields').get('headers')),
                 ('method',  method,  template['request'].get('method'))]:
                print_verbose('Check if {} matches...'.format(item))
                if rules.get(item) == 'match': # Standard match
                    if value != value_templ:
                        matches = False
                        print_verbose('{} not matching!'.format(item))
                elif rules.get(item): # Regex match
                    print_verbose('REGEX searching for {} in {}'.format(rules['uri'].get(item), value))
                    if not re.search(rules.get(item), value):
                        matches = False
                        print_verbose('{} not matching (regex match)!'.format(item))

            if rules.get('uri'):
                print_verbose('Parsing URL: {}'.format(request_url))
                url = urlparse(request_url)
                url_template = urlparse(template['request']['uri'])

                for item, value, value_templ in \
                    [('host',       url.hostname, url_template.hostname),
                     ('path',       url.path,     url_template.path),
                     ('scheme',     url.scheme,   url_template.scheme),
                     ('query_form', url.query,    url_template.query)]:

                    print_verbose('Check if URI {} matches...'.format(item))
                    if rules['uri'].get(item) == 'match': # Standard match
                        if value != value_templ:
                            matches = False
                            print_verbose('{} not matching!'.format(item))
                    elif rules['uri'].get(item): # Regex match
                        print_verbose('REGEX searching for {} in {}'.format(rules['uri'].get(item), value))
                        if not re.search(rules['uri'].get(item), value):
                            matches = False
                            print_verbose('{} not matching (regex match)!').format(item)

            if matches:
                print_verbose('Rule matches!')
                return template
            else:
                print_verbose('Rule does not match!')
    print_verbose('No match found')
    return False


class ReplayHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self, method='GET', body=True): # ToDo: implement other methods besides GET?
        try:
            try:
                hostname = self.headers.getheader('Host')
                if not hostname:
                    hostname = 'localhost'
                if self.path.startswith("http"):
                    url = self.path
                else:
                    url = 'http://{}{}'.format(hostname, self.path)
                sio = StringIO.StringIO()
                sio.write('====BEGIN REQUEST=====\n{}\n{} {} {}\n'.format(url, self.command, self.path, self.request_version))
                # Prepare header information for matching
                dict_headers = {}
                for line in self.headers.headers:
                    line_split = line.split(':', 1)
                    line_parts = [o.strip() for o in line_split]
                    if len(line_parts) == 2:
                        if line_parts[0].startswith('X-'):
                            pass
                        else:
                            sio.write(line)
                            dict_headers[line_split[0]] = line_split[1].strip()
                sio.write('====END REQUEST=======')
                print_verbose(sio.getvalue()) # Verbose request output
                # Get matching template for this request
                matched_template = get_matching_template('GET', hostname, dict_headers, url)
                if matched_template:
                    self.send_response(matched_template['response']['fields']['code'])
                    for header in matched_template['response']['fields']['headers']:
                        self.send_header(header, matched_template['response']['fields']['headers'][header])
                    self.end_headers()
                    self.wfile.write(matched_template['response']['fields']['content'])
                    print_verbose(matched_template['response']['fields']['content'])
                    return
                else:
                    self.send_error(404, 'no matching template found')
            finally:
                sio.close()
        except IOError:
            self.send_error(404, 'file not found')


class ProxyHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self, body=True): # ToDo: implement other methods besides GET?
        sent = False
        try:
            req = None
            resp = None
            sio = StringIO.StringIO()
            try:
                if args.static_url: # Static remote if client is not proxy-aware
                    url = args.static_url
                    hostname = urlparse(url).hostname
                else:
                    hostname = self.headers.getheader('Host')
                    if not hostname:
                        hostname = 'localhost'
                    if self.path.startswith("http"):
                        url = self.path
                    else:
                        url = 'http://{}{}'.format(hostname, self.path)
                req = urllib2.Request(url=url)
                sio.write('====BEGIN REQUEST=====\n{}\n{} {} {}\n'.format(url, self.command, self.path, self.request_version))
                dict_req_headers = {}
                for line in self.headers.headers:
                    line_split = line.split(':', 1)
                    line_parts = [o.strip() for o in line_split]
                    if len(line_parts) == 2:
                        if line_parts[0].startswith('X-'):
                            pass
                        elif line_parts[0] in ('Connection','User-Agent'):
                            pass
                        else:
                            sio.write(line)
                            dict_req_headers[line_split[0]] = line_split[1]
                            req.add_header(*line_parts)
                sio.write('====END REQUEST=======')
                print_verbose(sio.getvalue()) # Verbose request output
                try:
                    resp = urllib2.urlopen(req)
                except urllib2.HTTPError as e:
                    if e.getcode():
                        resp = e
                    else:
                        self.send_error(599, 'Error proxying: {}'.format(unicode(e)))
                        sent = True
                        return
                self.send_response(resp.getcode())
                respheaders = resp.info()
                dict_resp_headers = {}
                for line in respheaders.headers:
                    line_parts = line.split(':', 1)
                    if len(line_parts) == 2:
                        self.send_header(*line_parts)
                        dict_resp_headers[line_parts[0]] = line_parts[1].strip()
                self.end_headers()
                sent = True
                if body:
                    # Try decompressing (gzip)
                    data = resp.read()
                    try:
                        data = decompress(data)
                    except:
                        pass
                    if not data.startswith("!!binary"): # Exclude binary files from getting template data
                        try:
                            dump_file = dump_data(hostname, url, 'GET', dict_req_headers, dict_resp_headers, resp.getcode(), data, rules)
                            self.wfile.write('httpysim retrieved URL: {}\n'.format(url))
                            self.wfile.write('Successfully dumped the following data:\n')
                            self.wfile.write(data)
                            print 'Retrieved URL: {}'.format(url)
                            print 'Successfully dumped data to {}'.format(dump_file)
                        except:
                            self.send_error(404, 'Error dumping data: {}'.format(str(data)))
                return
            finally:
                if resp:
                    resp.close()
                sio.close()
        except IOError as e:
            if not sent:
                self.send_error(404, 'Error trying to proxy: {}'.format(str(e)))


def dump_url_directly(url):
    hostname = urlparse(url).hostname
    try:
        req = urllib2.Request(url)
        resp = urllib2.urlopen(req)
        dict_req_headers = req.unredirected_hdrs
    except urllib2.HTTPError as e:
        if e.getcode():
            resp = e
        else:
            print 'Error retrieving URL: {}'.format(unicode(e))
            return
    respheaders = resp.info()
    dict_resp_headers = {}
    for line in respheaders.headers:
        line_parts = line.split(':', 1)
        if len(line_parts) == 2:
            dict_resp_headers[line_parts[0]] = line_parts[1].strip()
    # Try decompressing (gzip)
    data = resp.read()
    try:
        data = decompress(data)
    except:
        pass
    try:
        dump_file = dump_data(hostname, url, 'GET', dict_req_headers, dict_resp_headers, resp.getcode(), data, rules)
        print 'Retrieved URL: {}'.format(url)
        print 'Successfully dumped data to {}'.format(dump_file)
    except:
        print 'Error dumping data: {}'.format(str(data))
    return


def parse_args(argv=sys.argv[1:]):
    parser = argparse.ArgumentParser(description='httpysim, a http server for dumping and replaying HTTP requests')
    parser.add_argument('--port', dest='port', type=int, default=9090,
                      help='Listen on specified port (default: 9090)')
    parser.add_argument('--mode', dest='server_mode', choices=['replay', 'dump', 'direct'], default='replay',
                      help='Run httpysim as server for dumping or replaying templates. Also it can try to fetch a URL directly (--static-url required)')
    parser.add_argument('--static-url', dest='static_url', type=str, default = None,
                      help='Static remote URL for clients that are not proxy-aware or for direct mode')
    parser.add_argument('-v', '--verbose', dest='verbose', help='Increase output verbosity', action='store_true')
    args = parser.parse_args(argv)
    return args


def main(argv=sys.argv[1:]):
    global args
    args = parse_args(argv)
    server_address = ('', args.port)
    if args.server_mode == 'direct':
        if args.static_url:
            dump_url_directly(args.static_url)
            sys.exit(0)
        else:
            print 'Parameter --static-url is required!'
            sys.exit(1)
    elif args.server_mode == 'dump':
        httpd = HTTPServer(server_address, ProxyHTTPRequestHandler)
    else:
        httpd = HTTPServer(server_address, ReplayHTTPRequestHandler)
    print 'http server is starting on port {}...'.format(args.port)
    print 'httpysim is running in {} mode...'.format(args.server_mode)
    httpd.serve_forever()

if __name__ == '__main__':
    main()
