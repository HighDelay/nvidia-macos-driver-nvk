#!/bin/bash
# Every regular file copied by the installer must be covered by SHA256SUMS.
# Backported from NullMoth 28afa39; keep the fork's escaped checksum filenames.
# This checks coverage, not digests: run shasum --strict -c first.
set -uo pipefail
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH
ROOT=${1:?usage: sums_complete.sh pkgroot}
cd "$ROOT" || exit 1
[ -f SHA256SUMS ] && [ ! -L SHA256SUMS ] || { echo "STOP: missing regular SHA256SUMS"; exit 1; }
# shasum already requires the system Perl. File::Find is a core module; no
# downloaded interpreter is needed on the installing Mac. Do not follow links.
/usr/bin/perl - <<'PERL'
use strict;
use warnings;
use File::Find ();

open my $manifest, '<:raw', 'SHA256SUMS' or die "STOP: cannot read SHA256SUMS: $!\n";
my %listed;
while (my $line = <$manifest>) {
    $line =~ s/\n\z//;
    next if $line eq '' || $line =~ /^#/;
    $line =~ /\A(\\?)[0-9a-fA-F]{64} [ *](.+)\z/s
        or die "STOP: invalid checksum record\n";
    my ($escaped, $name) = ($1, $2);
    if ($escaped) {
        $name =~ /\A(?:[^\\]|\\[\\nr])*\z/s
            or die "STOP: invalid checksum filename escape\n";
        $name =~ s/\\([\\nr])/$1 eq 'n' ? "\n" : $1 eq 'r' ? "\r" : "\\"/ge;
    }
    $name !~ m{\A/|(?:\A|/)\.\.(?:/|\z)} && index($name, "\0") < 0
        or die "STOP: checksum path leaves the package\n";
    $name = join '/', grep { length && $_ ne '.' } split m{/+}, $name;
    length($name) or die "STOP: empty checksum filename\n";
    $listed{$name} = 1;
}
close $manifest or die "STOP: cannot close SHA256SUMS: $!\n";
keys(%listed) or die "STOP: empty checksum manifest\n";
my @missing;
my $count = 0;
{
    # A directory that cannot be enumerated must not silently hide payloads.
    local $SIG{__WARN__} = sub { die "STOP: cannot enumerate package: $_[0]" };
    File::Find::find({no_chdir => 1, wanted => sub {
        my $path = $File::Find::name;
        return if -l $path || !-f $path;
        $path =~ s{\A\./}{};
        return if $path eq 'SHA256SUMS';
        $count++;
        push @missing, $path unless $listed{$path};
    }}, '.');
}
if (@missing) {
    print "STOP: SHA256SUMS does not list ", scalar(@missing), " file(s):\n";
    for my $name ((sort @missing)[0 .. (@missing > 20 ? 19 : $#missing)]) {
        $name =~ s/\\/\\\\/g; $name =~ s/\n/\\n/g; $name =~ s/\r/\\r/g;
        print "  $name\n";
    }
    exit 1;
}
print "SHA256SUMS lists all $count files\n";
PERL
