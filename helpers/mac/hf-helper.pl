#!/usr/bin/perl
# HF Hub helper for Mac.
# Answers HF Hub's page — on this computer only (127.0.0.1) — with a signed snapshot:
# computer name, LAN IP, network link (cable speed / Wi-Fi) and SSD usage + health.
# Reads nothing else: no files, screens, keystrokes or browsing.
use strict;
use warnings;
use IO::Socket::INET;
use Digest::SHA qw(hmac_sha256_hex);
use MIME::Base64 qw(encode_base64);

my $CONF = $ENV{HF_HELPER_CONF} || '/Library/Application Support/HFHub/helper.conf';
my %cfg = read_conf($CONF);
die "helper.conf is missing device_id/secret\n" unless $cfg{device_id} && $cfg{secret};
my $PORT    = $cfg{port} || 47600;
my @ORIGINS = grep { length } split /\s*,\s*/, ($cfg{origins} || '');

my $server = IO::Socket::INET->new(
    LocalAddr => '127.0.0.1', LocalPort => $PORT, Listen => 10, ReuseAddr => 1, Proto => 'tcp',
) or die "cannot listen on 127.0.0.1:$PORT: $!\n";
$SIG{PIPE} = 'IGNORE';

while (my $client = $server->accept) {
    eval {
        local $SIG{ALRM} = sub { die "timeout\n" };
        alarm 10;
        handle($client);
        alarm 0;
    };
    close $client;
}

sub handle {
    my ($c) = @_;
    $c->autoflush(1);
    my $line = <$c>;
    return unless defined $line;
    my ($method, $path) = $line =~ m{^(\S+)\s+(\S+)};
    my %h;
    while (my $l = <$c>) {
        last if $l =~ /^\r?\n$/;
        $h{lc $1} = $2 if $l =~ /^([^:]+):\s*(.*?)\r?\n$/;
    }
    my $origin = $h{origin} // '';
    my $allowed = grep { $_ eq $origin } @ORIGINS;
    my $cors = "Vary: Origin\r\n";
    $cors .= "Access-Control-Allow-Origin: $origin\r\nAccess-Control-Allow-Methods: GET\r\n"
           . "Access-Control-Allow-Headers: Content-Type\r\nAccess-Control-Allow-Private-Network: true\r\n"
        if $allowed;

    if (($method // '') eq 'OPTIONS') {
        return reply($c, '204 No Content', $cors, '');
    }
    return reply($c, '404 Not Found', $cors, '') unless ($method // '') eq 'GET' && ($path // '') =~ m{^/snapshot};
    return reply($c, '403 Forbidden', $cors, '') unless $allowed;   # only HF Hub's own page

    my $payload = snapshot_json();
    my $body = sprintf('{"payload":"%s","sig":"%s"}',
                       encode_base64($payload, ''), hmac_sha256_hex($payload, $cfg{secret}));
    reply($c, '200 OK', $cors . "Content-Type: application/json\r\nCache-Control: no-store\r\n", $body);
}

sub reply {
    my ($c, $status, $headers, $body) = @_;
    print $c "HTTP/1.1 $status\r\n$headers" . "Content-Length: " . length($body)
           . "\r\nConnection: close\r\n\r\n$body";
}

sub snapshot_json {
    my $host = `/usr/sbin/scutil --get ComputerName 2>/dev/null`;
    chomp $host;

    my $iface = '';
    for (`/sbin/route -n get default 2>/dev/null`) { $iface = $1 if /interface:\s*(\S+)/ }
    my $ip = '';
    if ($iface =~ /^[\w.]+$/) { $ip = `/usr/sbin/ipconfig getifaddr $iface 2>/dev/null`; chomp $ip; }

    # Which kind of port is it? (Wi-Fi vs an Ethernet port/adapter)
    my ($port, $cur) = ('', '');
    for (`/usr/sbin/networksetup -listallhardwareports 2>/dev/null`) {
        $cur  = $1 if /^Hardware Port:\s*(.+?)\s*$/;
        $port = $cur if $iface && /^Device:\s*\Q$iface\E\s*$/;
    }
    my ($type, $mbps) = ('none', undef);
    if ($iface && $ip) {
        if ($port =~ /Wi-?Fi|AirPort/i) {
            $type = 'wifi';
        } else {
            my $if = `/sbin/ifconfig $iface 2>/dev/null`;
            if ($if =~ /status:\s*active/) {
                $type = 'cable';
                # e.g. "(1000baseT <full-duplex>)", "(2500Base-T ...)", "(10Gbase-T ...)", "(100baseTX ...)"
                $mbps = $2 ? $1 * 1000 : $1 if $if =~ /media:.*?\((\d+)(G?)base/i;
            }
        }
    }

    # SSD: the data volume holds everything you save; health from macOS's SMART check
    my $used;
    for (`/bin/df -kP /System/Volumes/Data 2>/dev/null`) { $used = $1 if m{\s(\d+)%\s+/System/Volumes/Data\s*$} }   # -P: capacity column only
    my $health = '';
    for (`/usr/sbin/diskutil info disk0 2>/dev/null`) { $health = $1 if /SMART Status:\s*(.+?)\s*$/ }
    $health = '' if $health =~ /Not Supported/i;

    my $disk = sprintf('{"name":"Macintosh HD","used_pct":%s,"health":%s,"wear_pct":null,"temp_c":null}',
                       defined $used ? $used : 'null', $health ne '' ? js($health) : 'null');
    return sprintf('{"device_id":%d,"ts":%d,"hostname":%s,"platform":"mac","ip":%s,"iface":%s,'
                 . '"link_type":"%s","link_mbps":%s,"disks":[%s]}',
                   $cfg{device_id}, time, js($host), js($ip), js($iface), $type,
                   defined $mbps ? $mbps : 'null', $disk);
}

sub js {   # JSON string
    my ($s) = @_;
    $s =~ s/(["\\])/\\$1/g;
    $s =~ s/([\x00-\x1f])/sprintf('\\u%04x', ord $1)/ge;
    return qq("$s");
}

sub read_conf {
    my ($file) = @_;
    my %c;
    open my $fh, '<', $file or die "cannot read $file: $!\n";
    while (<$fh>) { $c{$1} = $2 if /^\s*(\w+)\s*=\s*(.*?)\s*$/ }
    close $fh;
    return %c;
}
