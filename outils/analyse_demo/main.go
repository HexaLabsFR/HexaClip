// analyse_demo : lit une démo CS2 (.dem, .dem.zst, .dem.gz, .dem.bz2) et écrit en JSON
// la map, les joueurs, les rounds et chaque kill (tick, round, tueur, victime, arme, headshot…).
// Utilisé par HexaClip pour clipper les kills d'une démo : analyse_demo <démo> [sortie.json]
package main

import (
	"bufio"
	"compress/bzip2"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/klauspost/compress/zstd"
	dem "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs"
	common "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs/common"
	events "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs/events"
	msg "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs/msg"
)

type Joueur struct {
	SteamID string `json:"steamid"`
	Nom     string `json:"nom"`
	Equipe  string `json:"equipe"`
	Kills   int    `json:"kills"`
	Morts   int    `json:"morts"`
	HS      int    `json:"hs"`
	UserID  int    `json:"userid"` // spec_player <userid + 1> dans CS2
}

type Round struct {
	N       int    `json:"n"`
	Debut   int    `json:"debut"`   // fin du freezetime (tick)
	Fin     int    `json:"fin"`     // fin du round (tick)
	Gagnant string `json:"gagnant"` // CT / T
	ScoreCT int    `json:"score_ct"`
	ScoreT  int    `json:"score_t"`
}

type Kill struct {
	Tick        int    `json:"tick"`
	Round       int    `json:"round"`
	Tueur       string `json:"tueur"`
	TueurNom    string `json:"tueur_nom"`
	Victime     string `json:"victime"`
	VictimeNom  string `json:"victime_nom"`
	Arme        string `json:"arme"`
	HS          bool   `json:"hs"`
	Traverse    bool   `json:"traverse"`
	Fumee       bool   `json:"fumee"`
	SansLunette bool   `json:"sans_lunette"`
	Aveugle     bool   `json:"aveugle"`
	Allie       bool   `json:"allie"`
}

type Sortie struct {
	Carte    string    `json:"carte"`
	Serveur  string    `json:"serveur"`
	TickRate float64   `json:"tickrate"`
	DureeS   float64   `json:"duree_s"`
	Joueurs  []*Joueur `json:"joueurs"`
	Rounds   []*Round  `json:"rounds"`
	Kills    []*Kill   `json:"kills"`
	Erreur   string    `json:"erreur,omitempty"`
}

func ouvrir(chemin string) (io.Reader, func(), error) {
	f, err := os.Open(chemin)
	if err != nil {
		return nil, nil, err
	}
	br := bufio.NewReaderSize(f, 1<<20)
	low := strings.ToLower(chemin)
	switch {
	case strings.HasSuffix(low, ".zst"):
		z, err := zstd.NewReader(br)
		if err != nil {
			f.Close()
			return nil, nil, err
		}
		return z, func() { z.Close(); f.Close() }, nil
	case strings.HasSuffix(low, ".gz"):
		z, err := gzip.NewReader(br)
		if err != nil {
			f.Close()
			return nil, nil, err
		}
		return z, func() { z.Close(); f.Close() }, nil
	case strings.HasSuffix(low, ".bz2"):
		return bzip2.NewReader(br), func() { f.Close() }, nil
	}
	return br, func() { f.Close() }, nil
}

func equipe(t common.Team) string {
	switch t {
	case common.TeamCounterTerrorists:
		return "CT"
	case common.TeamTerrorists:
		return "T"
	}
	return ""
}

func sid(p *common.Player) string {
	if p == nil || p.SteamID64 == 0 {
		return ""
	}
	return fmt.Sprint(p.SteamID64)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage : analyse_demo <démo> [sortie.json] | analyse_demo --extraire <démo compressée> <sortie.dem>")
		os.Exit(2)
	}
	if os.Args[1] == "--extraire" && len(os.Args) == 4 {
		// CS2 ne lit que les .dem : on décompresse les démos Faceit (.dem.zst / .gz / .bz2)
		r, fermer, err := ouvrir(os.Args[2])
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		defer fermer()
		tmp := os.Args[3] + ".part"
		f, err := os.Create(tmp)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		_, err = io.Copy(f, r)
		f.Close()
		if err == nil {
			err = os.Rename(tmp, os.Args[3])
		}
		if err != nil {
			os.Remove(tmp)
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		os.Exit(0)
	}
	out := Sortie{Joueurs: []*Joueur{}, Rounds: []*Round{}, Kills: []*Kill{}}
	ecrire := func(code int) {
		var w io.Writer = os.Stdout
		if len(os.Args) > 2 {
			f, err := os.Create(os.Args[2])
			if err == nil {
				defer f.Close()
				w = f
			}
		}
		enc := json.NewEncoder(w)
		enc.SetIndent("", " ")
		_ = enc.Encode(out)
		os.Exit(code)
	}
	r, fermer, err := ouvrir(os.Args[1])
	if err != nil {
		out.Erreur = err.Error()
		ecrire(1)
	}
	defer fermer()

	p := dem.NewParser(r)
	defer p.Close()
	joueurs := map[string]*Joueur{}
	joueur := func(pl *common.Player) *Joueur {
		id := sid(pl)
		if id == "" {
			return nil
		}
		j, ok := joueurs[id]
		if !ok {
			j = &Joueur{SteamID: id}
			joueurs[id] = j
			out.Joueurs = append(out.Joueurs, j)
		}
		if pl.Name != "" {
			j.Nom = pl.Name
		}
		if e := equipe(pl.Team); e != "" {
			j.Equipe = e
		}
		j.UserID = pl.UserID
		return j
	}
	p.RegisterNetMessageHandler(func(m *msg.CDemoFileHeader) {
		out.Carte = m.GetMapName()
		out.Serveur = m.GetServerName()
	})
	var cur *Round
	live := func() bool {
		gs := p.GameState()
		return gs.IsMatchStarted() && !gs.IsWarmupPeriod()
	}
	p.RegisterEventHandler(func(e events.RoundFreezetimeEnd) {
		if !live() {
			return
		}
		n := p.GameState().TotalRoundsPlayed() + 1
		if cur != nil && cur.N == n {
			cur.Debut = p.GameState().IngameTick()
			return
		}
		cur = &Round{N: n, Debut: p.GameState().IngameTick()}
		out.Rounds = append(out.Rounds, cur)
	})
	p.RegisterEventHandler(func(e events.RoundEnd) {
		if cur == nil || !live() {
			return
		}
		cur.Fin = p.GameState().IngameTick()
		cur.Gagnant = equipe(e.Winner)
		if e.WinnerState != nil && e.LoserState != nil {
			if e.Winner == common.TeamCounterTerrorists {
				cur.ScoreCT, cur.ScoreT = e.WinnerState.Score(), e.LoserState.Score()
			} else {
				cur.ScoreT, cur.ScoreCT = e.WinnerState.Score(), e.LoserState.Score()
			}
		}
	})
	p.RegisterEventHandler(func(e events.Kill) {
		if !live() || e.Victim == nil || cur == nil {
			return
		}
		// kills hors d'un round joué (échauffement, round au couteau…) ignorés
		n := p.GameState().TotalRoundsPlayed() + 1
		if n != cur.N && !(cur.Fin > 0 && n == cur.N+1) {
			return
		}
		if n != cur.N {
			n = cur.N // kill juste après la fin du round (avant le décompte)
		}
		k := &Kill{Tick: p.GameState().IngameTick(), Round: n, HS: e.IsHeadshot, Traverse: e.PenetratedObjects > 0,
			Fumee: e.ThroughSmoke, SansLunette: e.NoScope, Aveugle: e.AttackerBlind}
		if e.Weapon != nil {
			k.Arme = e.Weapon.String()
		}
		joueur(e.Victim)
		k.Victime, k.VictimeNom = sid(e.Victim), e.Victim.Name
		if e.Killer != nil {
			joueur(e.Killer)
			k.Tueur, k.TueurNom = sid(e.Killer), e.Killer.Name
			k.Allie = e.Killer.Team == e.Victim.Team
		}
		out.Kills = append(out.Kills, k)
	})
	err = p.ParseToEnd()
	out.TickRate = p.TickRate()
	out.DureeS = p.CurrentTime().Seconds()
	for _, pl := range p.GameState().Participants().Playing() {
		joueur(pl)
	}
	// garder les kills joués pendant un round (après le freezetime), puis compter les stats
	debut := map[int]int{}
	for _, r := range out.Rounds {
		debut[r.N] = r.Debut
	}
	gardes := []*Kill{}
	for _, k := range out.Kills {
		if d, ok := debut[k.Round]; ok && k.Tick >= d {
			gardes = append(gardes, k)
			if v, ok := joueurs[k.Victime]; ok {
				v.Morts++
			}
			if t, ok := joueurs[k.Tueur]; ok && !k.Allie && k.Tueur != k.Victime {
				t.Kills++
				if k.HS {
					t.HS++
				}
			}
		}
	}
	out.Kills = gardes
	if err != nil && len(out.Kills) == 0 {
		out.Erreur = err.Error()
		ecrire(1)
	}
	ecrire(0)
}
