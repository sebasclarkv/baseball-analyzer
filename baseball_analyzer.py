#!/usr/bin/env python3
"""
Herramienta de Análisis de Probabilidades de Bateo
Analiza estadísticas de bateadores basadas en sus últimos partidos
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import pybaseball as pyb
from datetime import datetime, timedelta
import pandas as pd
import threading

# Suprimir warnings de pybaseball
pyb.cache.enable()


class BaseballAnalyzer:
    def __init__(self, root):
        self.root = root
        self.root.title("Analizador de Probabilidades de Bateo")
        self.root.geometry("950x800")
        
        # Colores modernos y suaves
        self.bg_color = '#e8eef5'
        self.primary_color = '#4a90e2'
        self.secondary_color = '#7b68ee'
        self.accent_color = '#50c878'
        self.text_color = '#1a1a1a'
        self.card_bg = '#ffffff'
        self.shadow_color = '#d0d7de'
        
        self.root.configure(bg=self.bg_color)
        
        # Configurar estilo
        style = ttk.Style()
        style.theme_use('clam')
        
        # Estilos personalizados
        style.configure('Title.TLabel', background=self.bg_color, foreground=self.text_color, 
                       font=('Helvetica', 18, 'bold'))
        style.configure('Subtitle.TLabel', background=self.bg_color, foreground=self.secondary_color, 
                       font=('Helvetica', 10, 'italic'))
        style.configure('Card.TFrame', background=self.card_bg, relief='flat')
        style.configure('TLabel', background=self.card_bg, foreground=self.text_color)
        style.configure('TButton', background=self.primary_color, foreground='white', 
                       font=('Helvetica', 10, 'bold'), borderwidth=0)
        style.map('TButton', background=[('active', self.secondary_color)])
        
        # Frame principal con sombra
        shadow_frame = tk.Frame(root, bg=self.shadow_color)
        shadow_frame.place(x=22, y=22, width=906, height=756)
        
        main_frame = tk.Frame(root, bg=self.card_bg, padx=25, pady=25)
        main_frame.place(x=20, y=20, width=910, height=760)
        
        # Header con título y autor
        header_frame = tk.Frame(main_frame, bg=self.card_bg)
        header_frame.grid(row=0, column=0, columnspan=2, pady=(0, 25), sticky=(tk.W, tk.E))
        
        title_label = tk.Label(header_frame, text="⚾ Analizador de Probabilidades de Bateo", 
                               font=('Helvetica', 24, 'bold'), 
                               fg=self.primary_color, bg=self.card_bg)
        title_label.pack()
        
        author_label = tk.Label(header_frame, text="By: @sebastianclarkv", 
                               font=('Helvetica', 11, 'italic'), 
                               fg=self.secondary_color, bg=self.card_bg)
        author_label.pack(pady=(5, 0))
        
        # Nombre del bateador
        input_label1 = tk.Label(main_frame, text="Nombre del Bateador:", 
                               font=('Helvetica', 12, 'bold'), fg=self.text_color, bg=self.card_bg)
        input_label1.grid(row=1, column=0, sticky=tk.W, pady=10)
        
        self.player_name_var = tk.StringVar()
        entry_frame1 = tk.Frame(main_frame, bg='#f8f9fa', highlightbackground=self.shadow_color, 
                               highlightthickness=1)
        entry_frame1.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=10, padx=5)
        
        self.player_entry = tk.Entry(entry_frame1, textvariable=self.player_name_var, 
                                     width=30, font=('Helvetica', 12), 
                                     bg='#f8f9fa', fg=self.text_color,
                                     relief='flat', borderwidth=0)
        self.player_entry.pack(padx=8, pady=8)
        
        # Cantidad de juegos
        input_label2 = tk.Label(main_frame, text="Últimos N juegos:", 
                               font=('Helvetica', 12, 'bold'), fg=self.text_color, bg=self.card_bg)
        input_label2.grid(row=2, column=0, sticky=tk.W, pady=10)
        
        self.games_var = tk.StringVar(value="20")
        spinbox_frame = tk.Frame(main_frame, bg='#f8f9fa', highlightbackground=self.shadow_color, 
                                highlightthickness=1)
        spinbox_frame.grid(row=2, column=1, sticky=tk.W, pady=10, padx=5)
        
        self.games_spinbox = ttk.Spinbox(spinbox_frame, from_=1, to=162, 
                                         textvariable=self.games_var, width=12, 
                                         font=('Helvetica', 12))
        self.games_spinbox.pack(padx=5, pady=5)
        
        # Botón de análisis con sombra
        button_shadow = tk.Frame(main_frame, bg=self.shadow_color)
        button_shadow.grid(row=3, column=0, columnspan=2, pady=(20, 0))
        
        self.analyze_button = tk.Button(button_shadow, text="🔍 Analizar", 
                                       command=self.analyze_player,
                                       bg=self.primary_color, fg='white',
                                       font=('Helvetica', 13, 'bold'),
                                       relief='flat', cursor='hand2',
                                       padx=40, pady=12,
                                       activebackground=self.secondary_color)
        self.analyze_button.pack(padx=2, pady=2)
        
        # Barra de progreso
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        # Área de resultados
        results_label = tk.Label(main_frame, text="📊 Resultados:", 
                                font=('Helvetica', 14, 'bold'), 
                                fg=self.text_color, bg=self.card_bg)
        results_label.grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(15, 8))
        
        # Frame con sombra para el área de texto
        text_shadow = tk.Frame(main_frame, bg=self.shadow_color)
        text_shadow.grid(row=6, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 15))
        
        self.result_text = scrolledtext.ScrolledText(text_shadow, width=85, height=20, 
                                                     font=('Courier', 11),
                                                     bg='#fafbfc', fg=self.text_color,
                                                     relief='flat', borderwidth=0,
                                                     padx=12, pady=12)
        self.result_text.pack(padx=2, pady=2, fill=tk.BOTH, expand=True)
        
        # Botón de copiar con sombra
        copy_shadow = tk.Frame(main_frame, bg=self.shadow_color)
        copy_shadow.grid(row=7, column=0, columnspan=2, pady=(5, 0))
        
        self.copy_button = tk.Button(copy_shadow, text="📋 Copiar Resultados", 
                                    command=self.copy_results, state='disabled',
                                    bg=self.accent_color, fg='white',
                                    font=('Helvetica', 12, 'bold'),
                                    relief='flat', cursor='hand2',
                                    padx=30, pady=10,
                                    activebackground='#45b369',
                                    disabledforeground='#cccccc')
        self.copy_button.pack(padx=2, pady=2)
        
        # Configurar pesos de las columnas
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(6, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
    
    def analyze_player(self):
        """Analiza las probabilidades del bateador"""
        player_name = self.player_name_var.get().strip()
        
        if not player_name:
            messagebox.showwarning("Advertencia", "Por favor ingresa el nombre del bateador")
            return
        
        try:
            games = int(self.games_var.get())
            if games < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Por favor ingresa un número válido de juegos")
            return
        
        # Ejecutar análisis en un hilo separado
        thread = threading.Thread(target=self._perform_analysis, args=(player_name, games))
        thread.daemon = True
        thread.start()
    
    def _perform_analysis(self, player_name, games):
        """Realiza el análisis en segundo plano"""
        self.root.after(0, self._start_progress)
        
        try:
            # Buscar jugador
            self.root.after(0, lambda: self.result_text.delete(1.0, tk.END))
            self.root.after(0, lambda: self.result_text.insert(tk.END, 
                           f"Buscando datos de {player_name}...\n"))
            
            # Obtener datos del jugador
            current_year = datetime.now().year
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')
            
            # Buscar jugador en la base de datos
            players = pyb.playerid_lookup(player_name.split()[1] if ' ' in player_name 
                                         else player_name, 
                                         player_name.split()[0] if ' ' in player_name else '')
            
            if players.empty:
                self.root.after(0, lambda: messagebox.showerror("Error", 
                               f"No se encontró al jugador: {player_name}"))
                return
            
            player_id = players.iloc[0]['key_mlbam']
            
            # Obtener estadísticas de bateo
            stats = pyb.statcast_batter(start_date, end_date, player_id)
            
            if stats.empty:
                self.root.after(0, lambda: messagebox.showerror("Error", 
                               "No se encontraron estadísticas recientes"))
                return
            
            # Ordenar por fecha descendente
            stats = stats.sort_values('game_date', ascending=False)
            
            # Obtener los últimos N juegos únicos
            unique_dates = stats['game_date'].unique()
            if len(unique_dates) > games:
                last_n_dates = unique_dates[:games]
                stats = stats[stats['game_date'].isin(last_n_dates)]
            
            # Calcular probabilidades
            analysis = self._calculate_probabilities(stats, player_name, games)
            
            self.root.after(0, lambda: self._display_results(analysis))
            
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Error", 
                           f"Error al analizar: {str(e)}"))
        finally:
            self.root.after(0, self._stop_progress)
    
    def _calculate_probabilities(self, stats, player_name, games):
        """Calcula las probabilidades basadas en los datos"""
        # Filtrar solo eventos completos (no cada pitch)
        stats_events = stats[stats['events'].notna()].copy()
        
        if len(stats_events) == 0:
            return None
        
        # Contar juegos únicos
        unique_games = stats_events['game_date'].nunique()
        
        # Contar apariciones al plato (plate appearances = eventos completos)
        total_pa = len(stats_events)
        
        # Contar eventos específicos
        hits = len(stats_events[stats_events['events'].isin(['single', 'double', 'triple', 'home_run'])])
        home_runs = len(stats_events[stats_events['events'] == 'home_run'])
        walks = len(stats_events[stats_events['events'] == 'walk'])
        strikeouts = len(stats_events[stats_events['events'].isin(['strikeout', 'strikeout_double_play'])])
        outs = len(stats_events[stats_events['events'].isin(['field_out', 'force_out', 'grounded_into_double_play',
                                                              'fielders_choice_out', 'double_play', 'triple_play',
                                                              'sac_fly', 'sac_bunt'])])
        
        # Outs sin swing (looking strikeouts)
        looking_k = len(stats_events[(stats_events['events'].isin(['strikeout'])) & 
                                     (stats_events['description'] == 'called_strike')])
        
        # Calcular probabilidades por aparición al plato
        hit_prob = (hits / total_pa) * 100 if total_pa > 0 else 0
        hr_prob = (home_runs / total_pa) * 100 if total_pa > 0 else 0
        bb_prob = (walks / total_pa) * 100 if total_pa > 0 else 0
        k_prob = (strikeouts / total_pa) * 100 if total_pa > 0 else 0
        out_prob = (outs / total_pa) * 100 if total_pa > 0 else 0
        looking_prob = (looking_k / total_pa) * 100 if total_pa > 0 else 0
        
        # Estadísticas avanzadas
        avg_exit_velo = stats['launch_speed'].mean() if 'launch_speed' in stats else 0
        avg_launch_angle = stats['launch_angle'].mean() if 'launch_angle' in stats else 0
        
        # Calcular promedio REAL de apariciones al plato por juego
        avg_pa_per_game = total_pa / unique_games if unique_games > 0 else 4
        
        # Predicción para el próximo juego (típicamente 3-5 apariciones)
        estimated_ab = round(avg_pa_per_game)
        predicted_hits = (hit_prob / 100) * estimated_ab
        predicted_hr = (hr_prob / 100) * estimated_ab
        predicted_walks = (bb_prob / 100) * estimated_ab
        predicted_strikeouts = (k_prob / 100) * estimated_ab
        predicted_outs = (out_prob / 100) * estimated_ab
        
        analysis = {
            'player_name': player_name,
            'games': games,
            'unique_games': unique_games,
            'total_pa': total_pa,
            'hits': hits,
            'home_runs': home_runs,
            'walks': walks,
            'strikeouts': strikeouts,
            'outs': outs,
            'looking_k': looking_k,
            'hit_prob': hit_prob,
            'hr_prob': hr_prob,
            'bb_prob': bb_prob,
            'k_prob': k_prob,
            'out_prob': out_prob,
            'looking_prob': looking_prob,
            'avg_exit_velo': avg_exit_velo,
            'avg_launch_angle': avg_launch_angle,
            'avg_pa_per_game': avg_pa_per_game,
            'estimated_ab': estimated_ab,
            'predicted_hits': predicted_hits,
            'predicted_hr': predicted_hr,
            'predicted_walks': predicted_walks,
            'predicted_strikeouts': predicted_strikeouts,
            'predicted_outs': predicted_outs
        }
        
        return analysis
    
    def _display_results(self, analysis):
        """Muestra los resultados en el área de texto"""
        self.result_text.delete(1.0, tk.END)
        
        if not analysis:
            self.result_text.insert(tk.END, "No se pudieron calcular las probabilidades\n")
            return
        
        result = f"""
╔═══════════════════════════════════════════════════════════════════════╗
║             ANÁLISIS DE PROBABILIDADES DE BATEO                      ║
║                     By: @sebastianclarkv                              ║
╚═══════════════════════════════════════════════════════════════════════╝

🏃 JUGADOR: {analysis['player_name']}
📅 Período: Últimos {analysis['games']} juegos solicitados 
           ({analysis['unique_games']} juegos analizados, {analysis['total_pa']} apariciones)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 PROBABILIDADES POR APARICIÓN AL PLATO:

  🎯 HIT:                            {analysis['hit_prob']:.1f}%
  💥 HOME RUN:                       {analysis['hr_prob']:.1f}%
  🚶 Base por Bolas (BB):            {analysis['bb_prob']:.1f}%
  ⚡ Ponche (K):                     {analysis['k_prob']:.1f}%
  📉 Out:                            {analysis['out_prob']:.1f}%
  👀 Ponche observando:              {analysis['looking_prob']:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔮 PREDICCIÓN PARA EL PRÓXIMO JUEGO:

  Apariciones al plato esperadas:    {analysis['estimated_ab']}
  
  📈 Resultados probables:
    • Hits:                          {analysis['predicted_hits']:.2f}
    • Home Runs:                     {analysis['predicted_hr']:.2f}
    • Bases por Bolas:               {analysis['predicted_walks']:.2f}
    • Ponches:                       {analysis['predicted_strikeouts']:.2f}
    • Outs:                          {analysis['predicted_outs']:.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 ESTADÍSTICAS HISTÓRICAS (Últimos {analysis['games']} juegos):

  Total de Hits:                     {analysis['hits']}
  Total de Home Runs:                {analysis['home_runs']}
  Total de Bases por Bolas:          {analysis['walks']}
  Total de Ponches:                  {analysis['strikeouts']}
  Total de Outs:                     {analysis['outs']}
  Ponches observando:                {analysis['looking_k']}
  Promedio apariciones/juego:        {analysis['avg_pa_per_game']:.1f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚙️  MÉTRICAS AVANZADAS:

  Velocidad de salida promedio:      {analysis['avg_exit_velo']:.1f} mph
  Ángulo de lanzamiento promedio:    {analysis['avg_launch_angle']:.1f}°

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⏰ Análisis generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        self.result_text.insert(tk.END, result)
        self.copy_button.config(state='normal')
    
    def copy_results(self):
        """Copia los resultados al portapapeles"""
        result = self.result_text.get(1.0, tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(result)
        messagebox.showinfo("Éxito", "Resultados copiados al portapapeles")
    
    def _start_progress(self):
        """Inicia la barra de progreso"""
        self.analyze_button.config(state='disabled')
        self.progress.start(10)
    
    def _stop_progress(self):
        """Detiene la barra de progreso"""
        self.progress.stop()
        self.analyze_button.config(state='normal')


def main():
    root = tk.Tk()
    app = BaseballAnalyzer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
